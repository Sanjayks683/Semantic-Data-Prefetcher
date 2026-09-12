// ============================================================================
// delta_generator.sv - Block address and block-to-block delta.
//
// The delta is computed at full block-address width and then checked before it
// is narrowed to DELTA_WIDTH. A jump too large to represent is reported on
// delta_ovf rather than silently wrapping into a bogus small delta, which would
// otherwise train the table on a correlation that never existed.
// ============================================================================

`timescale 1ns / 1ps

import ngram_types_pkg::*;

module delta_generator (
    input  logic                          clk,
    input  logic                          rst,
    input  logic [ADDR_WIDTH-1:0]         mem_addr_in,
    input  logic                          mem_valid,
    output logic signed [DELTA_WIDTH-1:0] delta_comb,
    output logic [BLOCK_ADDR_WIDTH-1:0]   curr_block_comb,
    output logic                          delta_valid_comb,
    output logic                          delta_ovf_comb
);

    logic [BLOCK_ADDR_WIDTH-1:0] prev_block;
    logic                        has_prev;

    assign curr_block_comb = mem_addr_in[ADDR_WIDTH-1:BLOCK_OFFSET_BITS];

    wire signed [BLOCK_ADDR_WIDTH-1:0] full_delta =
        $signed(curr_block_comb) - $signed(prev_block);

    // The value fits in DELTA_WIDTH bits exactly when every discarded bit, plus
    // the sign bit of what remains, is identical.
    wire [BLOCK_ADDR_WIDTH-DELTA_WIDTH:0] sign_span =
        full_delta[BLOCK_ADDR_WIDTH-1 : DELTA_WIDTH-1];
    wire fits = (&sign_span) || (~|sign_span);

    assign delta_comb       = full_delta[DELTA_WIDTH-1:0];
    assign delta_ovf_comb   = mem_valid && has_prev && !fits;
    assign delta_valid_comb = mem_valid && has_prev && fits;

    always_ff @(posedge clk or posedge rst) begin
        if (rst) begin
            prev_block <= '0;
            has_prev   <= 1'b0;
        end else if (mem_valid) begin
            prev_block <= curr_block_comb;
            has_prev   <= 1'b1;
        end
    end

endmodule : delta_generator
