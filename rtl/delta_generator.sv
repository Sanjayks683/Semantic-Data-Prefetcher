`timescale 1ns / 1ps

import ngram_types_pkg::*;

module delta_generator (
    input  logic                                clk,
    input  logic                                rst,
    input  logic [ADDR_WIDTH-1:0]               mem_addr_in,
    input  logic                                mem_valid,
    output logic signed [DELTA_WIDTH-1:0]       delta_comb,
    output logic [BLOCK_ADDR_WIDTH-1:0]         curr_block_comb,
    output logic                                delta_valid_comb
);

    logic [BLOCK_ADDR_WIDTH-1:0] prev_block;
    logic                        has_prev;

    assign curr_block_comb  = mem_addr_in[ADDR_WIDTH-1:BLOCK_OFFSET_BITS];
    assign delta_comb       = $signed(curr_block_comb[DELTA_WIDTH-1:0] - prev_block[DELTA_WIDTH-1:0]);
    assign delta_valid_comb = mem_valid && has_prev;

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
