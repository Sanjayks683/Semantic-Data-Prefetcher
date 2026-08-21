`timescale 1ns / 1ps

import ngram_types_pkg::*;

module confidence_fsm (
    input  table_entry_t                   current_entry,
    input  logic signed [DELTA_WIDTH-1:0]  actual_delta,
    input  logic [TAG_BITS-1:0]            expected_tag,
    output table_entry_t                   updated_entry
);

    wire                          entry_valid = current_entry.valid;
    wire [TAG_BITS-1:0]           entry_tag   = current_entry.tag;
    wire signed [DELTA_WIDTH-1:0] entry_delta = current_entry.pred_delta;
    wire [CONF_BITS-1:0]          entry_conf  = current_entry.conf;

    reg                          next_valid;
    reg [TAG_BITS-1:0]           next_tag;
    reg signed [DELTA_WIDTH-1:0] next_delta;
    reg [CONF_BITS-1:0]          next_conf;

    always @(*) begin
        if (entry_valid && (entry_tag == expected_tag)) begin
            if (entry_delta == actual_delta) begin
                next_valid = 1'b1;
                next_tag   = expected_tag;
                next_delta = actual_delta;
                if (entry_conf < CONF_STRONGLY_LIKELY)
                    next_conf = entry_conf + 1'b1;
                else
                    next_conf = CONF_STRONGLY_LIKELY;
            end else begin
                if (entry_conf > CONF_STRONGLY_UNLIKELY) begin
                    next_valid = 1'b1;
                    next_tag   = entry_tag;
                    next_delta = entry_delta;
                    next_conf  = entry_conf - 1'b1;
                end else begin
                    next_valid = 1'b1;
                    next_tag   = expected_tag;
                    next_delta = actual_delta;
                    next_conf  = CONF_WEAKLY_UNLIKELY;
                end
            end
        end else begin
            next_valid = 1'b1;
            next_tag   = expected_tag;
            next_delta = actual_delta;
            next_conf  = CONF_WEAKLY_UNLIKELY;
        end
    end

    assign updated_entry.valid      = next_valid;
    assign updated_entry.tag        = next_tag;
    assign updated_entry.pred_delta = next_delta;
    assign updated_entry.conf       = next_conf;

endmodule : confidence_fsm
