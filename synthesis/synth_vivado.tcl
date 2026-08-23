# ==============================================================================
# File: synth_vivado.tcl
# Description: Vivado Synthesis & Timing Closure script for N-Gram Prefetcher.
# Target: Xilinx Artix-7 (xc7a100tcsg324-1) / UltraScale+
# ==============================================================================

# 1. Create In-Memory Project
create_project -in_memory -part xc7a100tcsg324-1

# 2. Add SystemVerilog Source Files
read_verilog -sv ../rtl/ngram_types_pkg.sv
read_verilog -sv ../rtl/delta_generator.sv
read_verilog -sv ../rtl/history_shift_reg.sv
read_verilog -sv ../rtl/xor_hash.sv
read_verilog -sv ../rtl/sram_table.sv
read_verilog -sv ../rtl/confidence_fsm.sv
read_verilog -sv ../rtl/ngram_prefetcher.sv

# 3. Set Top Module
set_property top ngram_prefetcher [current_fileset]

# 4. Synthesize Design
synth_design -top ngram_prefetcher -part xc7a100tcsg324-1 -mode out_of_context

# 5. Create Timing Constraints (Target Clock: 250 MHz / 4.0ns Period)
create_clock -period 4.000 -name clk [get_ports clk]

# 6. Run Timing & Area Reports
report_utilization -file utilization_report.txt
report_timing_summary -file timing_report.txt
report_power -file power_report.txt

puts "\[SYNTHESIS\] Synthesis completed successfully! Reports generated in synthesis/ directory."
