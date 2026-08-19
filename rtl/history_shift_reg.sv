`timescale 1ns / 1ps

import ngram_types_pkg::*;

module history_shift_reg (
    input  logic                                clk,
    input  logic                                rst,
    input  logic signed [DELTA_WIDTH-1:0]       delta_in,
    input  logic                                shift_en,
    output logic [NGRAM_DEPTH*DELTA_WIDTH-1:0]  history_bus_comb,
    output logic                                history_valid_comb
);

    logic signed [DELTA_WIDTH-1:0] hist_regs [0:NGRAM_DEPTH-2];
    logic [$clog2(NGRAM_DEPTH+1)-1:0] valid_count;
    integer i;

    always_comb begin
        for (i = 0; i < NGRAM_DEPTH - 1; i = i + 1) begin
            history_bus_comb[i*DELTA_WIDTH +: DELTA_WIDTH] = hist_regs[i];
        end
        history_bus_comb[(NGRAM_DEPTH-1)*DELTA_WIDTH +: DELTA_WIDTH] = delta_in;
        history_valid_comb = (valid_count >= (NGRAM_DEPTH - 1)) && shift_en;
    end

    always_ff @(posedge clk or posedge rst) begin
        if (rst) begin
            for (i = 0; i < NGRAM_DEPTH - 1; i = i + 1) begin
                hist_regs[i] <= '0;
            end
            valid_count <= '0;
        end else if (shift_en) begin
            for (i = 0; i < NGRAM_DEPTH - 2; i = i + 1) begin
                hist_regs[i] <= hist_regs[i + 1];
            end
            if (NGRAM_DEPTH > 1) begin
                hist_regs[NGRAM_DEPTH - 2] <= delta_in;
            end

            if (valid_count < NGRAM_DEPTH - 1) begin
                valid_count <= valid_count + 1'b1;
            end
        end
    end

endmodule : history_shift_reg
