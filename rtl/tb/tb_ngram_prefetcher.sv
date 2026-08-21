`timescale 1ns / 1ps

import ngram_types_pkg::*;

module tb_ngram_prefetcher;

    logic                   clk;
    logic                   rst;
    logic [ADDR_WIDTH-1:0]  mem_addr_in;
    logic                   mem_valid;
    logic [ADDR_WIDTH-1:0]  prefetch_addr_out;
    logic                   prefetch_valid;

    ngram_prefetcher dut (
        .clk                (clk),
        .rst                (rst),
        .mem_addr_in        (mem_addr_in),
        .mem_valid          (mem_valid),
        .prefetch_addr_out  (prefetch_addr_out),
        .prefetch_valid     (prefetch_valid)
    );

    always #5 clk = ~clk;

    logic [ADDR_WIDTH-1:0] test_pattern [4];

    integer successful_prefetches;
    integer total_requests;
    integer iter, step;

    initial begin
        test_pattern[0] = 64'h0000_0000_1000_0000;
        test_pattern[1] = 64'h0000_0000_1000_0180;
        test_pattern[2] = 64'h0000_0000_1000_0340;
        test_pattern[3] = 64'h0000_0000_1000_0040;

        successful_prefetches = 0;
        total_requests = 0;

        clk         = 0;
        rst         = 1;
        mem_addr_in = '0;
        mem_valid   = 0;

        $display("\n=======================================================");
        $display("   STARTING SYSTEMVERILOG N-GRAM PREFETCHER TESTBENCH  ");
        $display("=======================================================\n");

        #20;
        rst = 0;
        #10;

        for (iter = 0; iter < 12; iter = iter + 1) begin
            for (step = 0; step < 4; step = step + 1) begin
                @(posedge clk);
                #1;
                mem_addr_in = test_pattern[step];
                mem_valid   = 1'b1;
                total_requests = total_requests + 1;

                #2;
                if (prefetch_valid) begin
                    logic [ADDR_WIDTH-1:0] expected_next;
                    expected_next = test_pattern[(step + 1) % 4];
                    $display("[TIME %0t ns] Addr: 0x%016h -> PREFETCH: 0x%016h (Expected: 0x%016h)", 
                             $time, test_pattern[step], prefetch_addr_out, expected_next);
                    if (prefetch_addr_out == expected_next) begin
                        successful_prefetches = successful_prefetches + 1;
                    end
                end

                @(posedge clk);
                #1;
                mem_valid = 1'b0;
                #10;
            end
        end

        #50;
        $display("\n=======================================================");
        $display("   TESTBENCH RESULTS SUMMARY                           ");
        $display("=======================================================");
        $display("  Total Memory Requests:     %0d", total_requests);
        $display("  Accurate Prefetches Fired: %0d", successful_prefetches);
        if (successful_prefetches > 0) begin
            $display("  STATUS: [PASSED] Hardware sequence learning confirmed!");
        end else begin
            $display("  STATUS: [FAILED] No prefetch fired.");
        end
        $display("=======================================================\n");

        $finish;
    end

endmodule : tb_ngram_prefetcher
