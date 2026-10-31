#include "replay_export.h"
#include "Engine/Core/Hooks/hooks.h"
#include "Engine/Core/Log/logging.h"
#include "Engine/Core/Platform/memory.h"
#include "Engine/Game/Build/addresses.h"
#include "Engine/Game/Build/20260929/replay_export.h"
#include "Engine/Game/Build/supported_build.h"
#include "Extension/UI/Overlay/gpu_diagnostics.h"
#include <algorithm>
#include <array>
#include <atomic>
#include <cstring>
#include <vector>
#include <Windows.h>
#include <combaseapi.h>
#include <dxgi1_4.h>
#include <wrl/client.h>
#include <mfidl.h>
#include <mftransform.h>

namespace dingosdk::replay_export {
namespace {
namespace native = addr::replay_export;
// The encoder passes its arguments straight on to the codec, so all four
// register arguments are forwarded as they came.
using Submit = void (*)(std::uintptr_t encoder, std::uintptr_t pixels, std::uintptr_t pitch, std::uintptr_t unused);
std::atomic<Submit> original{};
std::atomic<std::uintptr_t> image{};

void submit(std::uintptr_t encoder, std::uintptr_t pixels, std::uintptr_t pitch, std::uintptr_t unused) {
    // Encoding is done on this thread before the call returns, so letting every
    // frame through also holds the renderer to the pace the encoder keeps.
    std::uintptr_t vtable{};
    if (memory::peek(encoder, vtable) && vtable == image.load() + native::replay_encoder_vtable)
        *reinterpret_cast<std::uint8_t*>(encoder + native::frame_permit) = 1;
    original.load()(encoder, pixels, pitch, unused);
}

// A hardware H.264 encoder only accepts frames from a device on its own GPU. On
// a PC with two GPUs (an AMD processor's integrated graphics beside a graphics
// card) the encoder the game gets first can belong to the GPU it does not render
// on, and the export fails with "Failed to save video".
using EnumTransforms = HRESULT(WINAPI*)(GUID, UINT32, const MFT_REGISTER_TYPE_INFO*, const MFT_REGISTER_TYPE_INFO*,
    IMFActivate***, UINT32*);
std::atomic<EnumTransforms> original_enum{};
constexpr GUID video_encoder_category{0xf79eac7d, 0xe545, 0x4387, {0xbd, 0xee, 0xd6, 0x47, 0xd7, 0xbd, 0xe4, 0x2a}};
constexpr GUID hardware_vendor_id{0x3aecb0cc, 0x035b, 0x4bcc, {0x81, 0x85, 0x2b, 0x8d, 0x55, 0x1e, 0xf3, 0xaf}};

// Media Foundation's MFT_ENUM_ADAPTER_LUID: given to MFTEnum2, only the encoders on that adapter.
constexpr GUID adapter_luid_attribute{0x1d39518c, 0xe220, 0x4da8, {0xa0, 0x7f, 0xba, 0x17, 0x25, 0x52, 0xd6, 0xb1}};
// MFTEnum2 is Windows 10 1703 and later, so looked up, not linked.
using EnumAdapterTransforms = HRESULT(WINAPI*)(GUID, UINT32, const MFT_REGISTER_TYPE_INFO*,
    const MFT_REGISTER_TYPE_INFO*, IMFAttributes*, IMFActivate***, UINT32*);
using CreateAttributes = HRESULT(WINAPI*)(IMFAttributes**, UINT32);
std::atomic<EnumAdapterTransforms> enum_for_adapter{};
std::atomic<CreateAttributes> create_attributes{};
// MFTEnum2 may call MFTEnumEx, hooked here, on its way.
thread_local bool enumerating{};

struct RenderAdapter {
    LUID luid{};
    UINT vendor{};
};
bool render_adapter(RenderAdapter& out) noexcept {
    if (!overlay::render_adapter_luid(out.luid)) return false;
    Microsoft::WRL::ComPtr<IDXGIFactory4> factory;
    Microsoft::WRL::ComPtr<IDXGIAdapter1> adapter;
    DXGI_ADAPTER_DESC1 description{};
    if (FAILED(CreateDXGIFactory1(IID_PPV_ARGS(&factory))) ||
        FAILED(factory->EnumAdapterByLuid(out.luid, IID_PPV_ARGS(&adapter))) || FAILED(adapter->GetDesc1(&description)))
        return false;
    out.vendor = description.VendorId;
    return true;
}

// A hardware encoder says which vendor's it is ("VEN_1002"); a software one says nothing (0).
constexpr UINT unknown_vendor = 0xFFFFFFFF;   // hardware, its vendor not given as VEN_
UINT hardware_vendor(IMFActivate* activate) noexcept {
    wchar_t vendor[64]{};
    UINT32 length{};
    if (!activate || FAILED(activate->GetString(hardware_vendor_id, vendor, 64, &length))) return 0;
    const wchar_t* const id = wcsstr(vendor, L"VEN_");
    return id ? static_cast<UINT>(wcstoul(id + 4, nullptr, 16)) : unknown_vendor;
}
void release(IMFActivate** list, UINT32 count) noexcept {
    for (UINT32 i = 0; i < count; ++i)
        if (list[i]) list[i]->Release();
    CoTaskMemFree(list);
}

// The hardware encoders in the list the game was given swapped for those Media Foundation lists
// for the render adapter's LUID, software encoders left as they were. Two GPUs of one vendor (an
// AMD processor's graphics beside an AMD card, #329) look alike by vendor: the integrated one's
// encoder came first and the export hung in it. False when the system cannot list by adapter or
// lists no hardware encoder for it, leaving the list alone.
bool keep_render_adapter(GUID category, UINT32 flags, const MFT_REGISTER_TYPE_INFO* input,
    const MFT_REGISTER_TYPE_INFO* output, const LUID& luid, IMFActivate*** activates, UINT32* count) noexcept {
    const auto enumerate = enum_for_adapter.load();
    const auto create = create_attributes.load();
    if (!enumerate || !create) return false;
    Microsoft::WRL::ComPtr<IMFAttributes> attributes;
    if (FAILED(create(&attributes, 1)) ||
        FAILED(attributes->SetBlob(adapter_luid_attribute, reinterpret_cast<const UINT8*>(&luid), sizeof luid)))
        return false;
    IMFActivate** found{};
    UINT32 found_count{};
    enumerating = true;
    const HRESULT listed = enumerate(category, flags, input, output, attributes.Get(), &found, &found_count);
    enumerating = false;
    if (FAILED(listed) || !found) return false;

    std::vector<IMFActivate*> kept, dropped;
    for (UINT32 i = 0; i < found_count; ++i) (hardware_vendor(found[i]) ? kept : dropped).push_back(found[i]);
    if (kept.empty()) {
        release(found, found_count);
        return false;
    }
    for (UINT32 i = 0; i < *count; ++i)
        (hardware_vendor((*activates)[i]) ? dropped : kept).push_back((*activates)[i]);
    auto* const list = static_cast<IMFActivate**>(CoTaskMemAlloc(kept.size() * sizeof(IMFActivate*)));
    if (!list) {
        release(found, found_count);
        return false;
    }
    std::copy(kept.begin(), kept.end(), list);
    for (auto* const activate : dropped)
        if (activate) activate->Release();
    CoTaskMemFree(found);
    CoTaskMemFree(*activates);
    *activates = list;
    *count = static_cast<UINT32>(kept.size());
    return true;
}

HRESULT WINAPI enum_transforms(GUID category, UINT32 flags, const MFT_REGISTER_TYPE_INFO* input,
    const MFT_REGISTER_TYPE_INFO* output, IMFActivate*** activates, UINT32* count) {
    const HRESULT result = original_enum.load()(category, flags, input, output, activates, count);
    if (enumerating || FAILED(result) || !IsEqualGUID(category, video_encoder_category) || !activates || !*activates ||
        !count)
        return result;
    RenderAdapter render;
    if (!render_adapter(render)) return result;
    if (std::none_of(*activates, *activates + *count, [](IMFActivate* activate) { return hardware_vendor(activate) != 0; }))
        return result;

    if (keep_render_adapter(category, flags, input, output, render.luid, activates, count)) {
        logging::write(logging::Level::info, logging::Channel::graphics,
            "Replay video export uses the hardware encoders of the graphics card the game renders on.");
        return result;
    }
    // Without a list by adapter, by vendor: right unless both GPUs are one vendor's.
    std::vector<IMFActivate*> kept, dropped;
    for (UINT32 i = 0; i < *count; ++i) {
        IMFActivate* const activate = (*activates)[i];
        const UINT vendor = hardware_vendor(activate);
        const bool foreign = vendor && vendor != unknown_vendor && vendor != render.vendor;
        (foreign ? dropped : kept).push_back(activate);
    }
    if (dropped.empty() || kept.empty()) return result;
    for (std::size_t i = 0; i < kept.size(); ++i) (*activates)[i] = kept[i];
    *count = static_cast<UINT32>(kept.size());
    for (auto* const activate : dropped) activate->Release();
    logging::write(logging::Level::info, logging::Channel::graphics,
        "Replay video export skipped hardware encoders that are not on the graphics card the game renders on.");
    return result;
}
} // namespace

bool start(std::uintptr_t base) noexcept {
    try {
        if (const auto mfplat = LoadLibraryW(L"mfplat.dll")) {
            enum_for_adapter = reinterpret_cast<EnumAdapterTransforms>(GetProcAddress(mfplat, "MFTEnum2"));
            create_attributes = reinterpret_cast<CreateAttributes>(GetProcAddress(mfplat, "MFCreateAttributes"));
            void* const target = reinterpret_cast<void*>(GetProcAddress(mfplat, "MFTEnumEx"));
            void* previous{};
            if (target && hook_prepare(target, reinterpret_cast<void*>(&enum_transforms), &previous) == HookOk) {
                original_enum = reinterpret_cast<EnumTransforms>(previous);
                if (hook_enable(target) != HookOk) hook_remove(target);
            }
        }

        std::array<unsigned char, native::submit_frame_prefix.size()> bytes{};
        if (!base || !memory::read_bytes(base + native::submit_frame, bytes.data(), bytes.size()) ||
            bytes != native::submit_frame_prefix) {
            logging::write(logging::Level::warning, logging::Channel::graphics,
                "Replay export frame pacing hook skipped: game code did not match the supported build.");
            return false;
        }
        image = base;
        auto* target = reinterpret_cast<void*>(base + native::submit_frame);
        void* previous{};
        if (hook_prepare(target, reinterpret_cast<void*>(&submit), &previous) != HookOk) return false;
        original = reinterpret_cast<Submit>(previous);
        if (hook_enable(target) != HookOk) {
            hook_remove(target);
            return false;
        }
        return true;
    } catch (...) {
        return false;
    }
}
} // namespace dingosdk::replay_export
