// ============================================================================
// ngram_prefetcher.sv - Top level of the semantic N-Gram delta prefetcher.
//
// One memory access per cycle in, at most one prefetch address out, everything
// combinational between them:
//
//   mem_addr_in -> delta -> shift window -> XOR hash -> table lookup -> prefetch
//
// The table entry consulted for the *previous* access is retrained with the
// delta that actually followed it, one cycle later. sram_table bypasses an
// in-flight write to the index being read, so a lookup landing on the entry
// being retrained this cycle sees the updated value - matching the behavioural
// model, which updates before it looks up.
// ============================================================================

`timescale 1ns / 1ps

import ngram_types_pkg::*;

module ngram_prefetcher (
    input  logic                   clk,
    input  logic                   rst,
    input  logic [ADDR_WIDTH-1:0]  mem_addr_in,
    input  logic                   mem_valid,
    output logic [ADDR_WIDTH-1:0]  prefetch_addr_out,
    output logic                   prefetch_valid,
    // Status: asserted when an access was skipped because the block delta did
    // not fit in DELTA_WIDTH bits. Useful for sizing the field against a real
    // workload; leave unconnected if not needed.
    output logic                   delta_overflow
);

    logic signed [DELTA_WIDTH-1:0]      current_delta;
    logic [BLOCK_ADDR_WIDTH-1:0]        current_block;
    logic                               delta_valid;
    logic                               delta_ovf;

    logic [NGRAM_DEPTH*DELTA_WIDTH-1:0] history_bus;
    logic                               history_valid;

    logic [INDEX_BITS-1:0]              lookup_index;
    logic [TAG_BITS-1:0]                lookup_tag;
    table_entry_t                       sram_read_entry;

    logic [INDEX_BITS-1:0]              prev_index;
    logic [TAG_BITS-1:0]                prev_tag;
    logic                               prev_valid;
    table_entry_t                       prev_sram_entry;
    table_entry_t                       fsm_updated_entry;

    assign delta_overflow = delta_ovf;

    wire                          read_valid      = sram_read_entry.valid;
    wire [TAG_BITS-1:0]           read_tag        = sram_read_entry.tag;
    wire signed [DELTA_WIDTH-1:0] read_pred_delta = sram_read_entry.pred_delta;
    wire [CONF_BITS-1:0]          read_conf       = sram_read_entry.conf;

    delta_generator u_delta_gen (
        .clk              (clk),
        .rst              (rst),
        .mem_addr_in      (mem_addr_in),
        .mem_valid        (mem_valid),
        .delta_comb       (current_delta),
        .curr_block_comb  (current_block),
        .delta_valid_comb (delta_valid),
        .delta_ovf_comb   (delta_ovf)
    );

    history_shift_reg u_shift_reg (
        .clk                (clk),
        .rst                (rst),
        .delta_in           (current_delta),
        .shift_en           (delta_valid),
        .flush              (delta_ovf),
        .history_bus_comb   (history_bus),
        .history_valid_comb (history_valid)
    );

    xor_hash u_hash_engine (
        .history_bus (history_bus),
        .table_index (lookup_index),
        .tag_out     (lookup_tag)
    );

    sram_table u_sram (
        .clk         (clk),
        .rst         (rst),
        .read_index  (lookup_index),
        .read_entry  (sram_read_entry),
        .write_en    (prev_valid && delta_valid),
        .write_index (prev_index),
        .write_entry (fsm_updated_entry)
    );

    confidence_fsm u_conf_fsm (
        .current_entry (prev_sram_entry),
        .actual_delta  (current_delta),
        .expected_tag  (prev_tag),
        .updated_entry (fsm_updated_entry)
    );

    // Remember which entry made this cycle's prediction so the next access can
    // retrain it against the delta that actually occurred.
    always_ff @(posedge clk or posedge rst) begin
        if (rst) begin
            prev_index      <= '0;
            prev_tag        <= '0;
            prev_valid      <= 1'b0;
            prev_sram_entry <= '0;
        end else if (delta_ovf) begin
            // Discontinuity: nothing meaningful to retrain against next cycle.
            prev_valid      <= 1'b0;
        end else if (delta_valid) begin
            prev_valid <= history_valid;
            if (history_valid) begin
                prev_index      <= lookup_index;
                prev_tag        <= lookup_tag;
                prev_sram_entry <= sram_read_entry;
            end
        end
    end

    wire signed [BLOCK_ADDR_WIDTH-1:0] signed_delta =
        {{(BLOCK_ADDR_WIDTH-DELTA_WIDTH){read_pred_delta[DELTA_WIDTH-1]}}, read_pred_delta};

    wire signed [BLOCK_ADDR_WIDTH:0] predicted_block_ext =
        $signed({1'b0, current_block}) + signed_delta;

    wire predicted_block_valid = (predicted_block_ext >= 0) &&
                                 (predicted_block_ext[BLOCK_ADDR_WIDTH] == 1'b0);

    wire [BLOCK_ADDR_WIDTH-1:0] predicted_block = predicted_block_ext[BLOCK_ADDR_WIDTH-1:0];

    always_comb begin
        if (delta_valid && history_valid &&
            read_valid &&
            (read_tag == lookup_tag) &&
            (read_conf >= CONF_BITS'(CONF_THRESHOLD)) &&
            predicted_block_valid) begin

            prefetch_addr_out = {predicted_block, {BLOCK_OFFSET_BITS{1'b0}}};
            prefetch_valid    = 1'b1;
        end else begin
            prefetch_addr_out = '0;
            prefetch_valid    = 1'b0;
        end
    end

endmodule : ngram_prefetcher
