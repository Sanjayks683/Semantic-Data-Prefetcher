// ============================================================================
// tb_ngram_prefetcher.sv - Self-checking testbench for the N-Gram prefetcher.
//
// Every phase asserts a specific property and increments an error counter on
// violation. The run fails loudly ($fatal) if any check fails, so a regression
// cannot be mistaken for a pass.
//
// Phases:
//   1  cold start        - silent until the window has NGRAM_DEPTH deltas
//   2  repeating cycle   - learns a 4-address loop, then predicts it exactly
//   3  constant stride   - learns a same-index window (exercises the RDW bypass)
//   4  irregular stream  - confidence must suppress speculation on noise
//   5  delta overflow    - reports the discontinuity, predicts nothing
//   6  reset mid-stream  - forgets the window, stays silent while rebuilding
//   7  retraining        - a saturated entry relearns a changed follower
// ============================================================================

`timescale 1ns / 1ps

import ngram_types_pkg::*;

module tb_ngram_prefetcher;

    localparam int CYCLE_LEN  = 4;
    localparam int WARMUP_REP = 4;
    localparam int TOTAL_REP  = 14;

    logic                  clk;
    logic                  rst;
    logic [ADDR_WIDTH-1:0] mem_addr_in;
    logic                  mem_valid;
    logic [ADDR_WIDTH-1:0] prefetch_addr_out;
    logic                  prefetch_valid;
    logic                  delta_overflow;

    // Sampled shortly before the committing clock edge.
    logic                  obs_pf;
    logic [ADDR_WIDTH-1:0] obs_addr;
    logic                  obs_ovf;

    int errors  = 0;
    int checks  = 0;
    int fired   = 0;
    int correct = 0;

    ngram_prefetcher dut (
        .clk               (clk),
        .rst               (rst),
        .mem_addr_in       (mem_addr_in),
        .mem_valid         (mem_valid),
        .prefetch_addr_out (prefetch_addr_out),
        .prefetch_valid    (prefetch_valid),
        .delta_overflow    (delta_overflow)
    );

    always #5 clk = ~clk;

    // ------------------------------------------------------------- helpers --

    task automatic drive(input logic [ADDR_WIDTH-1:0] addr);
        @(negedge clk);
        mem_addr_in = addr;
        mem_valid   = 1'b1;
        #1;
        obs_pf   = prefetch_valid;
        obs_addr = prefetch_addr_out;
        obs_ovf  = delta_overflow;
    endtask

    task automatic idle(input int cycles);
        @(negedge clk);
        mem_valid = 1'b0;
        repeat (cycles) @(posedge clk);
    endtask

    task automatic do_reset();
        @(negedge clk);
        mem_valid = 1'b0;
        rst       = 1'b1;
        repeat (3) @(posedge clk);
        @(negedge clk);
        rst = 1'b0;
        repeat (2) @(posedge clk);
    endtask

    function automatic void expect_eq(input logic cond, input string msg);
        checks++;
        if (!cond) begin
            errors++;
            $display("  [FAIL] %s", msg);
        end
    endfunction

    // Phase 1 ----------------------------------------------------------------
    task automatic run_phase1_cold_start();
        logic [ADDR_WIDTH-1:0] base;
        base = 64'h0000_0000_2000_0000;
        $display("\n[phase 1] cold start");

        for (int i = 0; i < NGRAM_DEPTH; i++) begin
            drive(base + i * BLOCK_SIZE_BYTES);
            expect_eq(!obs_pf, $sformatf(
                "access %0d fired a prefetch before the window was full", i));
        end
        idle(2);
        $display("  silent for the first %0d accesses", NGRAM_DEPTH);
    endtask

    // Phase 2 ----------------------------------------------------------------
    task automatic run_phase2_repeating_cycle();
        logic [ADDR_WIDTH-1:0] base;
        logic [ADDR_WIDTH-1:0] cycle [CYCLE_LEN];
        logic [ADDR_WIDTH-1:0] expected;
        int steady_fired;
        int steady_total;

        base         = 64'h0000_0000_1000_0000;
        steady_fired = 0;
        steady_total = 0;

        cycle[0] = base + 64'h000;
        cycle[1] = base + 64'h180;
        cycle[2] = base + 64'h340;
        cycle[3] = base + 64'h040;

        $display("\n[phase 2] repeating %0d-address cycle", CYCLE_LEN);
        do_reset();

        for (int rep = 0; rep < TOTAL_REP; rep++) begin
            for (int s = 0; s < CYCLE_LEN; s++) begin
                drive(cycle[s]);
                expected = cycle[(s + 1) % CYCLE_LEN];

                if (rep >= WARMUP_REP) begin
                    steady_total++;
                    if (obs_pf) begin
                        steady_fired++;
                        fired++;
                        if (obs_addr == expected) correct++;
                        expect_eq(obs_addr == expected, $sformatf(
                            "rep %0d step %0d: predicted 0x%h, expected 0x%h",
                            rep, s, obs_addr, expected));
                    end
                end
            end
        end
        idle(2);

        expect_eq(steady_fired == steady_total, $sformatf(
            "steady state fired only %0d of %0d prefetches; a fully learned cycle must predict every access",
            steady_fired, steady_total));
        $display("  steady state: %0d/%0d accesses prefetched",
                 steady_fired, steady_total);
    endtask

    // Phase 3 ----------------------------------------------------------------
    // A constant stride makes every access hash to the same index, so the entry
    // is read and written in the same cycle on every access. Without the
    // read-during-write bypass in sram_table the table trains a cycle behind.
    task automatic run_phase3_constant_stride();
        logic [ADDR_WIDTH-1:0] base;
        int first_fire;
        int wrong;
        int deadline;

        base       = 64'h0000_0000_3000_0000;
        first_fire = -1;
        wrong      = 0;
        deadline   = NGRAM_DEPTH + CONF_THRESHOLD;

        $display("\n[phase 3] constant stride (same-index / RDW bypass)");
        do_reset();

        for (int i = 0; i < 24; i++) begin
            drive(base + i * BLOCK_SIZE_BYTES);
            if (obs_pf) begin
                if (first_fire < 0) first_fire = i;
                fired++;
                if (obs_addr == base + (i + 1) * BLOCK_SIZE_BYTES) correct++;
                else wrong++;
            end
        end
        idle(2);

        expect_eq(first_fire >= 0, "constant stride never produced a prefetch");
        expect_eq(wrong == 0, $sformatf(
            "%0d incorrect predictions on a constant stride", wrong));

        // Window fills after NGRAM_DEPTH deltas; CONF_THRESHOLD more accesses
        // build confidence. Later than that means the table is training stale.
        expect_eq(first_fire >= 0 && first_fire <= deadline, $sformatf(
            "first prefetch at access %0d, expected by access %0d (table appears to be training a cycle behind)",
            first_fire, deadline));
        $display("  first prefetch at access %0d, %0d wrong", first_fire, wrong);
    endtask

    // Phase 4 ----------------------------------------------------------------
    task automatic run_phase4_irregular();
        logic [ADDR_WIDTH-1:0] base;
        logic [31:0] lfsr;
        int speculated;
        int total;

        base       = 64'h0000_0000_4000_0000;
        lfsr       = 32'hACE1_2345;
        speculated = 0;
        total      = 200;

        $display("\n[phase 4] irregular stream (confidence must suppress noise)");
        do_reset();

        for (int i = 0; i < total; i++) begin
            lfsr = {lfsr[30:0], lfsr[31] ^ lfsr[21] ^ lfsr[1] ^ lfsr[0]};
            drive(base + ({48'h0, lfsr[15:0]} * BLOCK_SIZE_BYTES));
            if (obs_pf) speculated++;
        end
        idle(2);

        // A non-repeating window should almost never reach the threshold. A few
        // hash collisions are tolerable; a flood means confidence is not gating.
        expect_eq(speculated * 100 <= total * 5, $sformatf(
            "%0d of %0d accesses speculated on an irregular stream (over 5 percent); the confidence counter is not gating",
            speculated, total));
        $display("  speculated on %0d/%0d irregular accesses", speculated, total);
    endtask

    // Phase 5 ----------------------------------------------------------------
    task automatic run_phase5_overflow();
        logic [ADDR_WIDTH-1:0] base;
        logic [ADDR_WIDTH-1:0] far;

        base = 64'h0000_0000_5000_0000;

        $display("\n[phase 5] delta overflow");
        do_reset();

        for (int i = 0; i < 8; i++) begin
            drive(base + i * BLOCK_SIZE_BYTES);
            expect_eq(!obs_ovf, $sformatf(
                "spurious overflow on in-range access %0d", i));
        end

        // Jump far beyond what a DELTA_WIDTH-bit signed delta can express.
        far = base + ((DELTA_MAX + 4096) * BLOCK_SIZE_BYTES);
        drive(far);
        expect_eq(obs_ovf, "an out-of-range block delta was not reported");
        expect_eq(!obs_pf, "a prefetch was issued from an out-of-range delta");

        // The window was flushed, so the next accesses must rebuild in silence.
        for (int i = 0; i < NGRAM_DEPTH - 1; i++) begin
            drive(far + (i + 1) * BLOCK_SIZE_BYTES);
            expect_eq(!obs_pf, $sformatf(
                "prefetch %0d fired before the flushed window refilled", i));
        end
        idle(2);
        $display("  overflow reported and window flushed");
    endtask

    // Phase 6 ----------------------------------------------------------------
    task automatic run_phase6_reset_midstream();
        logic [ADDR_WIDTH-1:0] base;
        base = 64'h0000_0000_6000_0000;

        $display("\n[phase 6] reset mid-stream");
        do_reset();

        for (int i = 0; i < 16; i++) begin
            drive(base + i * BLOCK_SIZE_BYTES);
        end

        do_reset();

        for (int i = 0; i < NGRAM_DEPTH; i++) begin
            drive(base + (100 + i) * BLOCK_SIZE_BYTES);
            expect_eq(!obs_pf, $sformatf(
                "prefetch fired %0d accesses after reset; state survived reset", i));
        end
        idle(2);
        $display("  state cleared correctly");
    endtask

    // Phase 7 ----------------------------------------------------------------
    // Retraining speed. Cycle A and cycle B share the window [+6, +7, -12] but
    // disagree on what follows it, so one table entry must be retrained from
    // one follower to the other. The mispredict path decrements and tests for
    // zero in the same step, so a saturated entry retrains in four visits; a
    // rule that tests before decrementing needs a fifth and fails this check.
    task automatic run_phase7_retrain();
        // Signed, so that a negative delta sign-extends rather than wrapping
        // into a huge unsigned value.
        longint signed blk;
        int dA [4];
        int dB [4];
        int first_correct;
        int wrong_fires;
        int budget;

        dA[0] =  6; dA[1] =  7; dA[2] = -12; dA[3] = -1;
        dB[0] =  6; dB[1] =  7; dB[2] = -12; dB[3] =  5;

        blk           = 64'sh0000_0000_7000_0000 >>> BLOCK_OFFSET_BITS;
        first_correct = -1;
        wrong_fires   = 0;
        // Visits needed to swing a saturated entry onto the new follower:
        //   CONF_MAX - 1     decay visits down to the retrain point
        //   1                visit that installs the new delta
        //   CONF_THRESHOLD-1 visits to build confidence back to the threshold
        //   + 1              the visit that finally fires correctly
        budget        = CONF_MAX + CONF_THRESHOLD - 1;

        $display("\n[phase 7] retraining after a follower changes");
        do_reset();

        drive(ADDR_WIDTH'(blk << BLOCK_OFFSET_BITS));

        // Learn cycle A until the shared window is saturated.
        for (int rep = 0; rep < 12; rep++) begin
            for (int s = 0; s < 4; s++) begin
                blk = blk + dA[s];
                drive(ADDR_WIDTH'(blk << BLOCK_OFFSET_BITS));
            end
        end

        // Switch to cycle B and watch the shared window (step 2) recover.
        for (int rep = 0; rep < 10; rep++) begin
            for (int s = 0; s < 4; s++) begin
                blk = blk + dB[s];
                drive(ADDR_WIDTH'(blk << BLOCK_OFFSET_BITS));

                if (s == 2) begin
                    if (obs_pf) begin
                        fired++;
                        if (obs_addr == ADDR_WIDTH'((blk + dB[3]) << BLOCK_OFFSET_BITS)) begin
                            correct++;
                            if (first_correct < 0) first_correct = rep;
                        end else begin
                            wrong_fires++;
                        end
                    end
                end
            end
        end
        idle(2);

        expect_eq(first_correct >= 0, "the entry never retrained to the new follower");
        expect_eq(first_correct >= 0 && first_correct <= budget, $sformatf(
            "retrained at repetition %0d, expected by repetition %0d (mispredict path is decrementing too slowly)",
            first_correct, budget));
        $display("  retrained at repetition %0d after %0d stale predictions",
                 first_correct, wrong_fires);
    endtask

    // -------------------------------------------------------------- verdict --
    task automatic report();
        $display("\n=======================================================");
        $display("   RESULTS");
        $display("=======================================================");
        $display("  Checks executed:     %0d", checks);
        $display("  Prefetches fired:    %0d", fired);
        $display("  Correct predictions: %0d", correct);
        if (fired > 0)
            $display("  Prediction accuracy: %0d percent", (correct * 100) / fired);
        $display("  Failures:            %0d", errors);
        $display("=======================================================");

        if (errors != 0) begin
            $display("  STATUS: [FAILED] %0d check(s) failed\n", errors);
            $fatal(1, "testbench failed");
        end

        $display("  STATUS: [PASSED] all %0d checks passed\n", checks);
        $finish;
    endtask

    // ----------------------------------------------------------------- main --
    initial begin
        clk         = 1'b0;
        rst         = 1'b1;
        mem_addr_in = '0;
        mem_valid   = 1'b0;

        $display("\n=======================================================");
        $display("   N-GRAM PREFETCHER - SELF-CHECKING TESTBENCH");
        $display("=======================================================");
        $display("  depth=%0d table=%0d tag=%0db conf=%0db thr=%0d delta=%0db",
                 NGRAM_DEPTH, TABLE_ENTRIES, TAG_BITS, CONF_BITS,
                 CONF_THRESHOLD, DELTA_WIDTH);

        do_reset();

        run_phase1_cold_start();
        run_phase2_repeating_cycle();
        run_phase3_constant_stride();
        run_phase4_irregular();
        run_phase5_overflow();
        run_phase6_reset_midstream();
        run_phase7_retrain();

        report();
    end

    initial begin
        #500000;
        $fatal(1, "testbench timeout - simulation did not complete");
    end

endmodule : tb_ngram_prefetcher
