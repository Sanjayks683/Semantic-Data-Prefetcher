# ==============================================================================
# constraints.xdc - Timing constraints for the N-Gram prefetcher.
#
# Read BEFORE synth_design so synthesis is actually driven by the target
# frequency. Applying create_clock afterwards produces a timing report against
# a netlist that was optimised without any timing goal at all.
# ==============================================================================

# Target: 250 MHz (4.0 ns period).
create_clock -period 4.000 -name clk [get_ports clk]

# Out-of-context block: budget a share of the period for whatever drives and
# receives these ports at the next level of hierarchy, so the reported slack is
# not an artefact of assuming zero external delay.
set_input_delay  -clock clk 1.000 [get_ports {rst mem_valid mem_addr_in[*]}]
set_output_delay -clock clk 1.000 [get_ports {prefetch_valid prefetch_addr_out[*] delta_overflow}]

# Reset is asynchronously asserted and synchronously released by the parent;
# it is not part of any timed path within this block.
set_false_path -from [get_ports rst]
