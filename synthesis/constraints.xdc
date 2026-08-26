
create_clock -period 4.000 -name clk [get_ports clk]

set_input_delay  -clock clk 1.000 [get_ports -filter {DIRECTION == IN && NAME != clk}]
set_output_delay -clock clk 1.000 [get_ports -filter {DIRECTION == OUT}]

set_false_path -from [get_ports rst]
