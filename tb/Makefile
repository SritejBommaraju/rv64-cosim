# Builds the Verilator co-sim model of rv64-ooo-core's ooo_top.
CORE      ?= $(abspath ../core)
BUILD     ?= $(abspath ../build/rtl)
MEM_BYTES ?= 1048576
RESET_PC  ?= 64\'h80000000
TRACE     ?= 0

RTL = $(CORE)/rtl/alu.sv $(CORE)/rtl/muldiv.sv $(CORE)/rtl/decode.sv \
      $(CORE)/rtl/rename/free_list.sv $(CORE)/rtl/rename/rename_map.sv $(CORE)/rtl/rename/rename.sv \
      $(CORE)/rtl/rename/prf.sv $(CORE)/rtl/ooo/rob.sv $(CORE)/rtl/lsq/lsq.sv \
      $(CORE)/rtl/ooo/ooo_mem.sv $(CORE)/rtl/ooo/csr.sv $(CORE)/rtl/ooo/ooo_core.sv $(CORE)/rtl/ooo/ooo_top.sv

VFLAGS = --cc --exe --build -O3 -Wno-fatal -Wno-lint -Wno-style -j 0 \
         --top-module ooo_top -Mdir $(BUILD) \
         -GRESET_PC=$(RESET_PC) -GMEM_BYTES=$(MEM_BYTES) -GHALT_ON_X31=0 \
         -CFLAGS "-O2 -I$(CURDIR) -DMEM_BYTES=$(MEM_BYTES)u"
ifeq ($(TRACE),1)
VFLAGS += --trace
endif

.PHONY: all clean
all: $(BUILD)/Vooo_top

$(BUILD)/Vooo_top: $(RTL) tb_cosim.cpp elf_loader.h
	@mkdir -p $(BUILD)
	verilator $(VFLAGS) $(RTL) $(CURDIR)/tb_cosim.cpp

clean:
	rm -rf $(BUILD)
