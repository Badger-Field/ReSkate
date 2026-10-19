#pragma once
#include <atomic>

// One installer at a time in the Mods folder while the game runs: a map a session needs
// (map_download.h) and a mod from the MODS page (mod_browser.h) each install a folder and then
// read, change and write mods.json, and two at once could lose one's change to mods.json or share
// the installer's staging folder. Whoever installs holds the claim from the install until mods.json
// is written. Nothing here blocks: a worker that has to wait for it says so with `pause`. No
// Windows here, so it is tested on Linux (Extension/Assets/Test/mods_writer_tests.cpp).
namespace dingosdk::mods_writer {
class Claim {
public:
    bool try_take() noexcept { return !held_.exchange(true, std::memory_order_acquire); }
    void release() noexcept { held_.store(false, std::memory_order_release); }
    bool held() const noexcept { return held_.load(std::memory_order_acquire); }
private:
    std::atomic<bool> held_{};
};
// The one the game's installers share.
inline Claim& claim() {
    static Claim shared;
    return shared;
}

// Releases a claim taken, when it goes.
class Hold {
public:
    Hold() = default;
    explicit Hold(Claim& taken) noexcept : claim_(&taken) {}
    Hold(Hold&& other) noexcept : claim_(other.claim_) { other.claim_ = nullptr; }
    Hold& operator=(Hold&&) = delete;
    ~Hold() { if (claim_) claim_->release(); }
    explicit operator bool() const noexcept { return claim_ != nullptr; }
private:
    Claim* claim_{};
};

// Waits until no one holds the claim, while `keep_going()` says to, calling `pause()` between
// looks. True once it is free (it is not taken: someone may take it next).
template<class KeepGoing, class Pause> bool wait_free(const Claim& claim, KeepGoing&& keep_going, Pause&& pause) {
    while (claim.held()) {
        if (!keep_going()) return false;
        pause();
    }
    return keep_going();
}
// Takes the claim, waiting for it the same way. An empty Hold when `keep_going()` stopped it.
template<class KeepGoing, class Pause> Hold take(Claim& claim, KeepGoing&& keep_going, Pause&& pause) {
    while (!claim.try_take()) {
        if (!keep_going()) return {};
        pause();
    }
    if (!keep_going()) {
        claim.release();
        return {};
    }
    return Hold(claim);
}
}
