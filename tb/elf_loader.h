// Minimal ELF64 little-endian loader: copies PT_LOAD segments through a byte-write
// callback and looks up symbols (tohost) in .symtab. Enough for bare-metal test ELFs.
#pragma once

#include <cstdint>
#include <cstring>
#include <fstream>
#include <functional>
#include <map>
#include <stdexcept>
#include <string>
#include <vector>

struct ElfImage {
    uint64_t entry = 0;
    std::map<std::string, uint64_t> symbols;
};

namespace elf_detail {
#pragma pack(push, 1)
struct Ehdr {
    uint8_t  ident[16];
    uint16_t type, machine;
    uint32_t version;
    uint64_t entry, phoff, shoff;
    uint32_t flags;
    uint16_t ehsize, phentsize, phnum, shentsize, shnum, shstrndx;
};
struct Phdr {
    uint32_t type, flags;
    uint64_t offset, vaddr, paddr, filesz, memsz, align;
};
struct Shdr {
    uint32_t name, type;
    uint64_t flags, addr, offset, size;
    uint32_t link, info;
    uint64_t addralign, entsize;
};
struct Sym {
    uint32_t name;
    uint8_t  info, other;
    uint16_t shndx;
    uint64_t value, size;
};
#pragma pack(pop)
constexpr uint32_t PT_LOAD = 1;
constexpr uint32_t SHT_SYMTAB = 2;
constexpr uint16_t EM_RISCV = 243;
}  // namespace elf_detail

inline ElfImage load_elf(const std::string& path,
                         const std::function<void(uint64_t, uint8_t)>& write_byte) {
    using namespace elf_detail;
    std::ifstream f(path, std::ios::binary);
    if (!f) throw std::runtime_error("cannot open " + path);
    std::vector<uint8_t> buf((std::istreambuf_iterator<char>(f)), std::istreambuf_iterator<char>());
    if (buf.size() < sizeof(Ehdr) || std::memcmp(buf.data(), "\x7f" "ELF", 4) != 0)
        throw std::runtime_error(path + ": not an ELF file");

    Ehdr eh;
    std::memcpy(&eh, buf.data(), sizeof eh);
    if (eh.ident[4] != 2 || eh.ident[5] != 1 || eh.machine != EM_RISCV)
        throw std::runtime_error(path + ": expected ELF64 little-endian RISC-V");

    ElfImage img;
    img.entry = eh.entry;

    for (unsigned i = 0; i < eh.phnum; i++) {
        Phdr ph;
        std::memcpy(&ph, buf.data() + eh.phoff + i * eh.phentsize, sizeof ph);
        if (ph.type != PT_LOAD) continue;
        for (uint64_t b = 0; b < ph.memsz; b++)
            write_byte(ph.paddr + b, b < ph.filesz ? buf[ph.offset + b] : 0);
    }

    for (unsigned i = 0; i < eh.shnum; i++) {
        Shdr sh;
        std::memcpy(&sh, buf.data() + eh.shoff + i * eh.shentsize, sizeof sh);
        if (sh.type != SHT_SYMTAB) continue;
        Shdr strtab;
        std::memcpy(&strtab, buf.data() + eh.shoff + sh.link * eh.shentsize, sizeof strtab);
        for (uint64_t off = 0; off + sizeof(Sym) <= sh.size; off += sizeof(Sym)) {
            Sym s;
            std::memcpy(&s, buf.data() + sh.offset + off, sizeof s);
            if (s.name == 0) continue;
            img.symbols[reinterpret_cast<const char*>(buf.data() + strtab.offset + s.name)] = s.value;
        }
    }
    return img;
}
