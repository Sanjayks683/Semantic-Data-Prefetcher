// ============================================================================
// ngram_prefetcher_pipe.sv - Pipelined N-Gram delta prefetcher.
//
// The single-cycle core (ngram_prefetcher.sv) puts two ~58-bit adders and an
// asynchronous table read in one combinational path, which limits it to about
// 70 MHz. This core splits that path into registered stages and produces the
// same decision for every access, PIPE_LATENCY cycles later:
//
//   S0  input register        mem_addr_in, mem_valid
//   S1  delta                 58-bit block subtract and range check
//   S2  window + hash         shift register, XOR fold -> index, tag
//   S3  table                 read, retrain, bypass, confidence decision
//   S4  target                58-bit add -> registered prefetch outputs
//
// Why it is exactly equivalent rather than approximately so: every interaction
// with the prediction table - the lookup for access k and the retraining write
// triggered by access k - happens in S3, in the same cycle, exactly as in the
// single-cycle core. Writes from earlier accesses have already committed at
// earlier clock edges, and the same-cycle case is covered by sram_table's
// read-during-write bypass. So no access ever observes table state that the
// single-cycle core would not have shown it. The only difference is when the
// answer appears.
//
// Throughput is unchanged: one access per cycle, back to back, with idle cycles
// (mem_valid low) allowed anywhere. `access_done` pulses once per accepted
// access when its result reaches the outputs, so a consumer can match results
// to accesses without counting cycles.
//
// rtl/tb/tb_pipe_equiv.sv checks this core against the single-cycle one cycle
// by cycle; tools/cosim_check.py --core pipe checks it against the model.
// ============================================================================

`timescale 1ns / 1ps

import ngram_types_pkg::*;

module ngram_prefetcher_pipe #(
    parameter int PIPE_LATENCY = 5      // documentation only; fixed by the stages below
) (
    input  logic                   clk,
    input  logic                   rst,
    input  logic [ADDR_WIDTH-1:0]  mem_addr_in,
    input  logic                   mem_valid,
    output logic [ADDR_WIDTH-1:0]  prefetch_addr_out,
    output logic                   prefetch_valid,
    output logic                   delta_overflow,
    output logic                   access_done
);

    if (PIPE_LATENCY != 5)
        $fatal(1, "ngram_prefetcher_pipe: PIPE_LATENCY is fixed at 5 by the stage structure");

    // ------------------------------------------------------------------- S0 --
    logic [ADDR_WIDTH-1:0] s0_addr;
    logic                  s0_valid;

    always_ff @(posedge clk or posedge rst) begin
        if (rst) begin
            s0_addr  <= '0;
            s0_valid <= 1'b0;
        end else begin
            s0_addr  <= mem_addr_in;
            s0_valid <= mem_valid;
        end
    end

    // ------------------------------------------------------------------- S1 --
    logic signed [DELTA_WIDTH-1:0] d_delta;
    logic [BLOCK_ADDR_WIDTH-1:0]   d_block;
    logic                          d_valid;
    logic                          d_ovf;

    delta_generator u_delta_gen (
        .clk              (clk),
        .rst              (rst),
        .mem_addr_in      (s0_addr),
        .mem_valid        (s0_valid),
        .delta_comb       (d_delta),
        .curr_block_comb  (d_block),
        .delta_valid_comb (d_valid),
        .delta_ovf_comb   (d_ovf)
    );

    logic signed [DELTA_WIDTH-1:0] s1_delta;
    logic [BLOCK_ADDR_WIDTH-1:0]   s1_block;
    logic                          s1_dvalid;
    logic                          s1_ovf;
    logic                          s1_valid;

    always_ff @(posedge clk or posedge rst) begin
        if (rst) begin
            s1_delta  <= '0;
            s1_block  <= '0;
            s1_dvalid <= 1'b0;
            s1_ovf    <= 1'b0;
            s1_valid  <= 1'b0;
        end else begin
            s1_delta  <= d_delta;
            s1_block  <= d_block;
            s1_dvalid <= d_valid;
            s1_ovf    <= d_ovf;
            s1_valid  <= s0_valid;
        end
    end

    // ------------------------------------------------------------------- S2 --
    logic [NGRAM_DEPTH*DELTA_WIDTH-1:0] h_bus;
    logic                               h_valid;
    logic [INDEX_BITS-1:0]              h_index;
    logic [TAG_BITS-1:0]                h_tag;

    history_shift_reg u_shift_reg (
        .clk                (clk),
        .rst                (rst),
        .delta_in           (s1_delta),
        .shift_en           (s1_dvalid),
        .flush              (s1_ovf),
        .history_bus_comb   (h_bus),
        .history_valid_comb (h_valid)
    );

    xor_hash u_hash_engine (
        .history_bus (h_bus),
        .table_index (h_index),
        .tag_out     (h_tag)
    );

    logic signed [DELTA_WIDTH-1:0] s2_delta;
    logic [BLOCK_ADDR_WIDTH-1:0]   s2_block;
    logic                          s2_dvalid;
    logic                          s2_ovf;
    logic                          s2_valid;
    logic                          s2_hvalid;
    logic [INDEX_BITS-1:0]         s2_index;
    logic [TAG_BITS-1:0]           s2_tag;

    always_ff @(posedge clk or posedge rst) begin
        if (rst) begin
            s2_delta  <= '0;
            s2_block  <= '0;
            s2_dvalid <= 1'b0;
            s2_ovf    <= 1'b0;
            s2_valid  <= 1'b0;
            s2_hvalid <= 1'b0;
            s2_index  <= '0;
            s2_tag    <= '0;
        end else begin
            s2_delta  <= s1_delta;
            s2_block  <= s1_block;
            s2_dvalid <= s1_dvalid;
            s2_ovf    <= s1_ovf;
            s2_valid  <= s1_valid;
            s2_hvalid <= h_valid;
            s2_index  <= h_index;
            s2_tag    <= h_tag;
        end
    end

    // ------------------------------------------------------------------- S3 --
    // Every table interaction lives here; see the header for why that makes the
    // pipeline exactly equivalent to the single-cycle core.
    table_entry_t          rd_entry;
    table_entry_t          fsm_entry;

    logic [INDEX_BITS-1:0] prev_index;
    logic [TAG_BITS-1:0]   prev_tag;
    logic                  prev_valid;
    table_entry_t          prev_entry;

    sram_table u_sram (
        .clk         (clk),
        .rst         (rst),
        .read_index  (s2_index),
        .read_entry  (rd_entry),
        .write_en    (prev_valid && s2_dvalid),
        .write_index (prev_index),
        .write_entry (fsm_entry)
    );

    confidence_fsm u_conf_fsm (
        .current_entry (prev_entry),
        .actual_delta  (s2_delta),
        .expected_tag  (prev_tag),
        .updated_entry (fsm_entry)
    );

    always_ff @(posedge clk or posedge rst) begin
        if (rst) begin
            prev_index <= '0;
            prev_tag   <= '0;
            prev_valid <= 1'b0;
            prev_entry <= '0;
        end else if (s2_ovf) begin
            prev_valid <= 1'b0;
        end else if (s2_dvalid) begin
            prev_valid <= s2_hvalid;
            if (s2_hvalid) begin
                prev_index <= s2_index;
                prev_tag   <= s2_tag;
                prev_entry <= rd_entry;
            end
        end
    end

    wire s3_hit_comb = s2_dvalid && s2_hvalid &&
                       rd_entry.valid &&
                       (rd_entry.tag == s2_tag) &&
                       (rd_entry.conf >= CONF_BITS'(CONF_THRESHOLD));

    logic signed [DELTA_WIDTH-1:0] s3_pred_delta;
    logic [BLOCK_ADDR_WIDTH-1:0]   s3_block;
    logic                          s3_hit;
    logic                          s3_ovf;
    logic                          s3_valid;

    always_ff @(posedge clk or posedge rst) begin
        if (rst) begin
            s3_pred_delta <= '0;
            s3_block      <= '0;
            s3_hit        <= 1'b0;
            s3_ovf        <= 1'b0;
            s3_valid      <= 1'b0;
        end else begin
            s3_pred_delta <= rd_entry.pred_delta;
            s3_block      <= s2_block;
            s3_hit        <= s3_hit_comb;
            s3_ovf        <= s2_ovf;
            s3_valid      <= s2_valid;
        end
    end

    // ------------------------------------------------------------------- S4 --
    wire signed [BLOCK_ADDR_WIDTH-1:0] ext_delta =
        {{(BLOCK_ADDR_WIDTH-DELTA_WIDTH){s3_pred_delta[DELTA_WIDTH-1]}}, s3_pred_delta};

    wire signed [BLOCK_ADDR_WIDTH:0] target_ext =
        $signed({1'b0, s3_block}) + ext_delta;

    wire target_ok = (target_ext >= 0) && (target_ext[BLOCK_ADDR_WIDTH] == 1'b0);

    always_ff @(posedge clk or posedge rst) begin
        if (rst) begin
            prefetch_addr_out <= '0;
            prefetch_valid    <= 1'b0;
            delta_overflow    <= 1'b0;
            access_done       <= 1'b0;
        end else begin
            if (s3_valid && s3_hit && target_ok) begin
                prefetch_addr_out <= {target_ext[BLOCK_ADDR_WIDTH-1:0], {BLOCK_OFFSET_BITS{1'b0}}};
                prefetch_valid    <= 1'b1;
            end else begin
                prefetch_addr_out <= '0;
                prefetch_valid    <= 1'b0;
            end
            delta_overflow <= s3_valid && s3_ovf;
            access_done    <= s3_valid;
        end
    end

endmodule : ngram_prefetcher_pipe
