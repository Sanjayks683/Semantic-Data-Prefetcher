
`timescale 1ns / 1ps

import ngram_types_pkg::*;

module tb_cosim;

    logic                  clk;
    logic                  rst;
    logic [ADDR_WIDTH-1:0] mem_addr_in;
    logic                  mem_valid;
    logic [ADDR_WIDTH-1:0] prefetch_addr_out;
    logic                  prefetch_valid;
    logic                  delta_overflow;

    logic                  obs_pf;
    logic [ADDR_WIDTH-1:0] obs_addr;

    integer                trace_fd;
    integer                out_fd;
    integer                scan_rc;
    integer                access_idx;
    integer                issued;

    logic [ADDR_WIDTH-1:0] addr;

    string trace_path;
    string out_path;

`ifdef NGRAM_PIPE
    logic   access_done;
    integer done_idx;

    ngram_prefetcher_pipe dut (
        .clk               (clk),
        .rst               (rst),
        .mem_addr_in       (mem_addr_in),
        .mem_valid         (mem_valid),
        .prefetch_addr_out (prefetch_addr_out),
        .prefetch_valid    (prefetch_valid),
        .delta_overflow    (delta_overflow),
        .access_done       (access_done)
    );

    always @(negedge clk) begin
        if (!rst && access_done) begin
            if (prefetch_valid) begin
                $fwrite(out_fd, "%0d %h\n", done_idx, prefetch_addr_out);
                issued++;
            end
            done_idx++;
        end
    end
`else
    ngram_prefetcher dut (
        .clk               (clk),
        .rst               (rst),
        .mem_addr_in       (mem_addr_in),
        .mem_valid         (mem_valid),
        .prefetch_addr_out (prefetch_addr_out),
        .prefetch_valid    (prefetch_valid),
        .delta_overflow    (delta_overflow)
    );
`endif

    always #5 clk = ~clk;

    task automatic drive(input logic [ADDR_WIDTH-1:0] a);
        @(negedge clk);
        mem_addr_in = a;
        mem_valid   = 1'b1;
        #1;
        obs_pf   = prefetch_valid;
        obs_addr = prefetch_addr_out;
    endtask

    initial begin
        clk         = 1'b0;
        rst         = 1'b1;
        mem_addr_in = '0;
        mem_valid   = 1'b0;
        access_idx  = 0;
        issued      = 0;
`ifdef NGRAM_PIPE
        done_idx    = 0;
`endif

        if (!$value$plusargs("trace=%s", trace_path))
            $fatal(1, "tb_cosim: missing +trace=<file>");
        if (!$value$plusargs("out=%s", out_path))
            $fatal(1, "tb_cosim: missing +out=<file>");

        trace_fd = $fopen(trace_path, "r");
        if (trace_fd == 0)
            $fatal(1, "tb_cosim: cannot open trace %s", trace_path);

        out_fd = $fopen(out_path, "w");
        if (out_fd == 0)
            $fatal(1, "tb_cosim: cannot open output %s", out_path);

        repeat (3) @(posedge clk);
        @(negedge clk);
        rst = 1'b0;
        repeat (2) @(posedge clk);

        scan_rc = $fscanf(trace_fd, "%h", addr);
        while (scan_rc == 1) begin
            drive(addr);
`ifndef NGRAM_PIPE
            if (obs_pf) begin
                $fwrite(out_fd, "%0d %h\n", access_idx, obs_addr);
                issued++;
            end
`endif
            access_idx++;
            scan_rc = $fscanf(trace_fd, "%h", addr);
        end

        @(negedge clk);
        mem_valid = 1'b0;
`ifdef NGRAM_PIPE

        while (done_idx < access_idx) @(negedge clk);
`endif
        repeat (2) @(posedge clk);

        $fclose(trace_fd);
        $fclose(out_fd);

        $display("tb_cosim: %0d accesses, %0d prefetches -> %s",
                 access_idx, issued, out_path);
        $finish;
    end

endmodule : tb_cosim
