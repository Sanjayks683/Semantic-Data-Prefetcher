// ============================================================================
// history_shift_reg.sv - Sliding window of the last NGRAM_DEPTH deltas.
//
// Only NGRAM_DEPTH-1 deltas are registered; the newest one is presented
// combinationally so the hash, lookup and prefetch decision all complete in the
// same cycle as the access that produced them.
//
// `flush` drops the window on a discontinuity (a delta too large to represent),
// so the hash is never taken over a window spanning a break in the stream.
// ============================================================================

`timescale 1ns / 1ps

import ngram_types_pkg::*;

module history_shift_reg (
    input  logic                                clk,
    input  logic                                rst,
    input  logic signed [DELTA_WIDTH-1:0]       delta_in,
    input  logic                                shift_en,
    input  logic                                flush,
    output logic [NGRAM_DEPTH*DELTA_WIDTH-1:0]  history_bus_comb,
    output logic                                history_valid_comb
);

    localparam int REG_COUNT  = NGRAM_DEPTH - 1;
    localparam int COUNT_BITS = $clog2(NGRAM_DEPTH + 1);

    logic signed [DELTA_WIDTH-1:0] hist_regs [REG_COUNT];
    logic [COUNT_BITS-1:0]         valid_count;

    always_comb begin
        for (int i = 0; i < REG_COUNT; i++) begin
            history_bus_comb[i*DELTA_WIDTH +: DELTA_WIDTH] = hist_regs[i];
        end
        history_bus_comb[REG_COUNT*DELTA_WIDTH +: DELTA_WIDTH] = delta_in;

        // Window is complete once REG_COUNT deltas are banked and a new one is
        // arriving on delta_in this cycle.
        history_valid_comb = shift_en && (valid_count >= COUNT_BITS'(REG_COUNT));
    end

    always_ff @(posedge clk or posedge rst) begin
        if (rst) begin
            for (int i = 0; i < REG_COUNT; i++) begin
                hist_regs[i] <= '0;
            end
            valid_count <= '0;
        end else if (flush) begin
            valid_count <= '0;
        end else if (shift_en) begin
            for (int i = 0; i < REG_COUNT - 1; i++) begin
                hist_regs[i] <= hist_regs[i + 1];
            end
            hist_regs[REG_COUNT - 1] <= delta_in;

            if (valid_count < COUNT_BITS'(REG_COUNT)) begin
                valid_count <= valid_count + 1'b1;
            end
        end
    end

endmodule : history_shift_reg
