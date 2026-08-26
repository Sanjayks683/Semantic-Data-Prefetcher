
`timescale 1ns / 1ps

import ngram_types_pkg::*;

module tb_pipe_equiv;

    localparam int LAT          = 5;
    localparam int RANDOM_STEPS = 300000;

    logic                  clk = 1'b0;
    logic                  rst = 1'b1;
    logic [ADDR_WIDTH-1:0] mem_addr_in = '0;
    logic                  mem_valid = 1'b0;

    logic [ADDR_WIDTH-1:0] c_addr, p_addr;
    logic                  c_valid, p_valid;
    logic                  c_ovf, p_ovf;
    logic                  p_done;

    ngram_prefetcher dut_c (
        .clk (clk), .rst (rst), .mem_addr_in (mem_addr_in), .mem_valid (mem_valid),
        .prefetch_addr_out (c_addr), .prefetch_valid (c_valid), .delta_overflow (c_ovf)
    );

    ngram_prefetcher_pipe dut_p (
        .clk (clk), .rst (rst), .mem_addr_in (mem_addr_in), .mem_valid (mem_valid),
        .prefetch_addr_out (p_addr), .prefetch_valid (p_valid), .delta_overflow (p_ovf),
        .access_done (p_done)
    );

    always #5 clk = ~clk;

    logic [ADDR_WIDTH-1:0] dl_addr  [LAT];
    logic                  dl_pf    [LAT];
    logic                  dl_ovf   [LAT];
    logic                  dl_acc   [LAT];

    always_ff @(posedge clk or posedge rst) begin
        if (rst) begin
            for (int i = 0; i < LAT; i++) begin
                dl_addr[i] <= '0; dl_pf[i] <= 1'b0; dl_ovf[i] <= 1'b0; dl_acc[i] <= 1'b0;
            end
        end else begin
            dl_addr[0] <= c_addr;
            dl_pf[0]   <= c_valid;
            dl_ovf[0]  <= c_ovf;
            dl_acc[0]  <= mem_valid;
            for (int i = 1; i < LAT; i++) begin
                dl_addr[i] <= dl_addr[i-1];
                dl_pf[i]   <= dl_pf[i-1];
                dl_ovf[i]  <= dl_ovf[i-1];
                dl_acc[i]  <= dl_acc[i-1];
            end
        end
    end

    int errors = 0, accesses = 0, prefetches = 0, overflows = 0, resets = 0;

    always @(negedge clk) begin
        if (!rst) begin
            if (p_done !== dl_acc[LAT-1]) begin
                errors++;
                if (errors <= 10) $display("  [FAIL] t=%0t access_done=%b expected %b", $time, p_done, dl_acc[LAT-1]);
            end else if (p_done) begin
                accesses++;
                if (p_valid !== dl_pf[LAT-1] || p_ovf !== dl_ovf[LAT-1] ||
                    (p_valid && p_addr !== dl_addr[LAT-1])) begin
                    errors++;
                    if (errors <= 10)
                        $display("  [FAIL] t=%0t pipe pf=%b addr=%h ovf=%b  single-cycle pf=%b addr=%h ovf=%b",
                                 $time, p_valid, p_addr, p_ovf, dl_pf[LAT-1], dl_addr[LAT-1], dl_ovf[LAT-1]);
                end
                if (p_valid) prefetches++;
                if (p_ovf)   overflows++;
            end
        end
    end

    int seed = 32'h5EED_2026;

    function automatic int rnd(input int lo, input int hi);
        int r;
        r = $random(seed);
        if (r < 0) r = -r;
        return lo + (r % (hi - lo + 1));
    endfunction

    task automatic access_blk(input longint signed blk);
        @(negedge clk);
        mem_addr_in = ADDR_WIDTH'(blk << BLOCK_OFFSET_BITS);
        mem_valid   = 1'b1;
    endtask

    task automatic idle(input int n);
        repeat (n) begin
            @(negedge clk);
            mem_valid = 1'b0;
        end
    endtask

    task automatic pulse_reset();
        @(negedge clk);
        mem_valid = 1'b0;
        rst = 1'b1;
        resets++;
        repeat (2) @(negedge clk);
        rst = 1'b0;
    endtask

    task automatic maybe_gap();
        if (rnd(0, 99) < 20) idle(rnd(1, 3));
    endtask

    longint signed blk = 64'sh0000_0000_0040_0000;

    initial begin
        $display("\n=======================================================");
        $display("   PIPELINE EQUIVALENCE: pipelined vs single-cycle core");
        $display("=======================================================");
        $display("  depth=%0d latency=%0d random steps=%0d", NGRAM_DEPTH, LAT, RANDOM_STEPS);

        repeat (3) @(negedge clk);
        rst = 1'b0;

        for (int r = 0; r < 16; r++) begin
            blk += 6;   access_blk(blk); maybe_gap();
            blk += 7;   access_blk(blk); maybe_gap();
            blk += -12; access_blk(blk); maybe_gap();
            blk += -1;  access_blk(blk); maybe_gap();
        end

        for (int i = 0; i < 64; i++) begin
            blk += 1; access_blk(blk);
        end

        blk += 64'sd40_000_000;  access_blk(blk);
        for (int i = 0; i < 16; i++) begin blk += 2; access_blk(blk); end

        pulse_reset();
        for (int i = 0; i < 16; i++) begin blk += 3; access_blk(blk); end

        for (int train = 1; train <= 2; train++) begin
            pulse_reset();
            blk = 64'sh0000_0000_0200_0000 + train * 64'sd10_000_000;
            access_blk(blk);
            for (int i = 0; i < NGRAM_DEPTH + train; i++) begin
                blk += 11 + train; access_blk(blk);
            end
            blk += 64'sd40_000_000; access_blk(blk);
            for (int i = 0; i < NGRAM_DEPTH + 3; i++) begin
                blk += 11 + train; access_blk(blk);
            end
            idle(LAT + 2);
        end

        begin
            int steps, mode, len, reps, s, n;
            int cyc [6];
            steps = 0;
            while (steps < RANDOM_STEPS) begin
                mode = rnd(0, 99);
                if (mode < 45) begin

                    len  = rnd(2, 6);
                    reps = rnd(3, 40);
                    for (int k = 0; k < len; k++) cyc[k] = rnd(-4000, 4000);
                    for (int r = 0; r < reps && steps < RANDOM_STEPS; r++)
                        for (int k = 0; k < len && steps < RANDOM_STEPS; k++) begin
                            if (rnd(0, 99) < 3) cyc[k] = rnd(-4000, 4000);
                            blk += cyc[k]; access_blk(blk); steps++;
                            if (rnd(0, 99) < 10) idle(rnd(1, 2));
                        end
                end else if (mode < 70) begin

                    s = rnd(-8, 8);
                    n = rnd(8, 200);
                    for (int i = 0; i < n && steps < RANDOM_STEPS; i++) begin
                        blk += s; access_blk(blk); steps++;
                    end
                end else if (mode < 92) begin

                    n = rnd(4, 80);
                    for (int i = 0; i < n && steps < RANDOM_STEPS; i++) begin
                        blk += rnd(-30000, 30000); access_blk(blk); steps++;
                        maybe_gap();
                    end
                end else if (mode < 98) begin

                    if (rnd(0, 1)) blk += longint'(rnd(100_000, 50_000_000));
                    else           blk -= longint'(rnd(100_000, 50_000_000));
                    if (blk < 0) blk = -blk;
                    access_blk(blk); steps++;
                end else begin
                    if (rnd(0, 99) < 25) pulse_reset();
                    else idle(rnd(1, 8));
                end
                if (blk < 64'sd1_000_000) blk += 64'sd10_000_000;
            end
        end

        idle(LAT + 3);

        $display("\n=======================================================");
        $display("  Accesses compared:   %0d", accesses);
        $display("  Prefetches compared: %0d", prefetches);
        $display("  Overflows compared:  %0d", overflows);
        $display("  Mid-stream resets:   %0d", resets);
        $display("  Mismatches:          %0d", errors);
        $display("=======================================================");

        if (errors != 0)
            $fatal(1, "pipelined core diverged from the single-cycle core");
        if (prefetches < 10000 || overflows < 100 || resets < 5)
            $fatal(1, "stimulus was too weak to be meaningful (pf=%0d ovf=%0d resets=%0d)",
                   prefetches, overflows, resets);

        $display("  STATUS: [PASSED] pipelined core matches single-cycle core on every cycle\n");
        $finish;
    end

endmodule : tb_pipe_equiv
