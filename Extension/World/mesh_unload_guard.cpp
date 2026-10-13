#include "mesh_unload_guard.h"
#include "mesh_registry.h"
#include "Engine/Core/Hooks/hooks.h"
#include "Engine/Core/Log/logging.h"
#include "Engine/Core/Platform/memory.h"
#include "Engine/Game/Build/addresses.h"
#include "Engine/Game/Build/20260929/mesh_variations.h"
#include <Windows.h>
#include <array>
#include <atomic>

namespace dingosdk::mesh_unload_guard {
namespace {
namespace mv = addr::mesh_variations;
// (Three arguments are read; the fourth is passed on as it came.)
using RemoveDatabases = std::uint64_t (*)(void*, void*, void*, std::uint64_t);
RemoveDatabases original{};
std::uintptr_t fault_at{}, key_at{}, resume_at{};
std::atomic<bool> installed{};
std::atomic<std::uint64_t> skips{}, recoveries{};

// Only the read of a mesh that is gone (a null one: rax is the entry's mesh). rbx is the entry,
// rdi the registry, whose lock this thread holds. When the mesh's name hash is found again, it
// goes in edx as the read would have put it, and the function goes on to take the entry out.
// Otherwise it goes on at its step to the next entry, passing that one over. Reads use
// ReadProcessMemory: no further exception is raised from inside this filter. Nothing else is
// this guard's.
int ours(EXCEPTION_POINTERS* info) noexcept {
    const auto& record = *info->ExceptionRecord;
    auto& context = *info->ContextRecord;
    if (record.ExceptionCode != EXCEPTION_ACCESS_VIOLATION || reinterpret_cast<std::uintptr_t>(record.ExceptionAddress) != fault_at ||
        context.Rax != 0)
        return EXCEPTION_CONTINUE_SEARCH;
    std::uint32_t low{}, inner{};
    if (key_at && memory::read(context.Rbx + 0x20, low) && memory::read(context.Rbx + 0x24, inner)) {
        const auto hash = mesh_registry::recover_mesh_hash(context.Rdi, low, inner,
            [](std::uintptr_t address, auto& value) { return memory::read(address, value); });
        if (hash) {
            context.Rdx = *hash;
            context.Rip = key_at;
            recoveries.fetch_add(1, std::memory_order_relaxed);
            return EXCEPTION_CONTINUE_EXECUTION;
        }
    }
    context.Rip = resume_at;
    skips.fetch_add(1, std::memory_order_relaxed);
    return EXCEPTION_CONTINUE_EXECUTION;
}
// Said once per unload that had any, with how many: a map can have hundreds.
__declspec(noinline) void note(std::uint64_t skipped_before, std::uint64_t recovered_before) noexcept {
    const auto skipped_now = skips.load(std::memory_order_relaxed), recovered_now = recoveries.load(std::memory_order_relaxed);
    try {
        if (recovered_now != recovered_before)
            logging::log(logging::Level::info, logging::Channel::runtime,
                "Level unload: {} mesh variation(s) had lost their mesh before their database was unloaded; they were taken "
                "out of the registry by their key ({} since the game started).", recovered_now - recovered_before, recovered_now);
        if (skipped_now != skipped_before)
            logging::log(logging::Level::warning, logging::Channel::runtime,
                "Level unload: {} mesh variation(s) had lost their mesh before their database was unloaded and could not be "
                "found in the registry; they were passed over ({} since the game started).", skipped_now - skipped_before, skipped_now);
    } catch (...) {}
}
std::uint64_t guarded(void* registry, void* second, void* list, std::uint64_t fourth) {
    const auto skipped_before = skips.load(std::memory_order_relaxed), recovered_before = recoveries.load(std::memory_order_relaxed);
    std::uint64_t result{};
    __try {
        result = original(registry, second, list, fourth);
    } __except (ours(GetExceptionInformation())) {
        // (Never entered: the filter either goes on in the function or passes the fault up.)
    }
    note(skipped_before, recovered_before);
    return result;
}
} // namespace

bool install(std::uintptr_t base) noexcept {
    if (installed.exchange(true)) return original != nullptr;
    if (!base) return false;
    std::array<unsigned char, 32> head{};
    std::array<unsigned char, 6> read{};
    std::array<unsigned char, 4> next{};
    if (!memory::peek(base + mv::remove_databases.rva, head) || head != mv::remove_databases.bytes ||
        !memory::peek(base + mv::mesh_hash_read, read) || read != mv::mesh_hash_read_bytes ||
        !memory::peek(base + mv::next_entry, next) || next != mv::next_entry_bytes) {
        logging::log(logging::Level::warning, logging::Channel::runtime,
            "Mesh unload guard: this build's mesh variation code differs; it is left as it is.");
        return false;
    }
    fault_at = base + mv::mesh_hash_read;
    resume_at = base + mv::next_entry;
    // Taking an entry out by its found key needs the instructions after the read as they were
    // checked; without them the guard only passes entries over.
    std::array<unsigned char, mv::key_low_read_bytes.size()> key_read{};
    key_at = memory::peek(base + mv::key_low_read, key_read) && key_read == mv::key_low_read_bytes ? base + mv::key_low_read : 0;
    const auto target = reinterpret_cast<void*>(base + mv::remove_databases.rva);
    if (hook_prepare(target, reinterpret_cast<void*>(&guarded), reinterpret_cast<void**>(&original)) != HookOk ||
        hook_enable(target) != HookOk) {
        original = nullptr;
        logging::log(logging::Level::warning, logging::Channel::runtime, "Mesh unload guard: the game's function could not be hooked.");
        return false;
    }
    return true;
}

std::uint64_t skipped() noexcept { return skips.load(std::memory_order_relaxed); }
std::uint64_t recovered() noexcept { return recoveries.load(std::memory_order_relaxed); }
} // namespace dingosdk::mesh_unload_guard
