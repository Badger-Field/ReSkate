// One installer at a time in the Mods folder (Extension/Assets/mods_writer.h): a map a session
// needs waits while a mod from the MODS page is installed, and the other way round.
#include "Extension/Assets/mods_writer.h"

#include <iostream>
#include <thread>
#include <vector>

using namespace dingosdk;

namespace {
int failures = 0;
void check(bool condition, const char* what) {
    if (condition) return;
    std::cerr << "FAIL: " << what << '\n';
    ++failures;
}
}

int main() {
    {
        mods_writer::Claim claim;
        check(!claim.held() && claim.try_take() && claim.held(), "a free claim is taken");
        check(!claim.try_take(), "and not taken twice");
        claim.release();
        check(!claim.held(), "and released");
        check(!mods_writer::Hold(), "an empty hold holds nothing");
        {
            check(claim.try_take(), "taken for a hold");
            mods_writer::Hold hold(claim);
            const auto moved = std::move(hold);
            check(!hold && moved && claim.held(), "a moved hold still holds, once");
        }
        check(!claim.held(), "released when the hold goes");
    }
    {
        // A map download waiting in its checking stage while a browser install runs.
        mods_writer::Claim claim;
        check(claim.try_take(), "the browser install takes it");
        int pauses{};
        const bool free = mods_writer::wait_free(claim, [] { return true; }, [&] { if (++pauses == 3) claim.release(); });
        check(free && pauses == 3 && !claim.held(), "the map waits until the install is done, and does not take it then");
        check(claim.try_take(), "taken again");
        pauses = 0;
        check(!mods_writer::wait_free(claim, [&] { return pauses < 2; }, [&] { ++pauses; }) && claim.held(),
              "a map whose session stopped waiting stops waiting too");
        check(!mods_writer::wait_free(mods_writer::Claim{}, [] { return false; }, [] {}), "and is not asked when stopped already");
        claim.release();
    }
    {
        mods_writer::Claim claim;
        claim.try_take();
        int pauses{};
        auto hold = mods_writer::take(claim, [] { return true; }, [&] { if (++pauses == 2) claim.release(); });
        check(hold && pauses == 2 && claim.held(), "take waits for the claim and holds it");
        bool going = true;
        auto other = mods_writer::take(claim, [&] { return going; }, [&] { going = false; });
        check(!other && claim.held(), "another that stops while waiting holds nothing");
    }
    {
        mods_writer::Claim claim;
        auto none = mods_writer::take(claim, [] { return false; }, [] {});
        check(!none && !claim.held(), "taken and given back at once when stopped already");
    }
    {
        // Many installers at once: each read-modify-write of the one file is whole.
        mods_writer::Claim claim;
        int file = 0; // as mods.json: read, changed and written back
        std::vector<std::thread> threads;
        for (int t = 0; t < 8; ++t)
            threads.emplace_back([&] {
                for (int i = 0; i < 2000; ++i) {
                    const auto hold = mods_writer::take(claim, [] { return true; }, [] { std::this_thread::yield(); });
                    const auto read = file;
                    std::this_thread::yield();
                    file = read + 1;
                }
            });
        for (auto& thread : threads) thread.join();
        check(file == 8 * 2000 && !claim.held(), "no change is lost");
    }
    if (failures) {
        std::cerr << failures << " failure(s)\n";
        return 1;
    }
    std::cout << "mods writer tests passed\n";
    return 0;
}
