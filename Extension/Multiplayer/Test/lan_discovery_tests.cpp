// Games on this player's own network (lan_discovery.h): a host answers a question with its
// listing; addresses outside private ranges are neither answered nor believed.
#include "Extension/Multiplayer/Direct/lan_discovery.h"

#include <chrono>
#include <cstdio>
#include <thread>

using namespace dingosdk::multiplayer;

namespace {
int failures = 0;
void check(bool ok, const char *what) {
    if (!ok) {
        ++failures;
        std::fprintf(stderr, "FAIL: %s\n", what);
    }
}
}

int main() {
    constexpr std::uint32_t loopback = 0x7f000001;
    check(private_ipv4(0x0a000001) && private_ipv4(0xac100001) && private_ipv4(0xac1fffff) && private_ipv4(0xc0a80101) &&
              private_ipv4(0xa9fe0001) && private_ipv4(loopback),
          "private ranges are private");
    check(!private_ipv4(0x08080808) && !private_ipv4(0xac200001) && !private_ipv4(0xac0fffff) && !private_ipv4(0xc0a90101) &&
              !private_ipv4(0x64400001), // 100.64/10, carrier-grade NAT, is not this network
          "public and carrier-grade NAT addresses are not");

    constexpr std::uint16_t port = 47791;
    LanDiscovery host(port), browser(port);
    host.announce(R"({"rs_name":"Dad's park","rs_code":"x"})");
    check(host.announcing() && host.problem().empty(), "the host listens");
    browser.search(loopback);
    std::uint64_t now = 1000;
    for (int i = 0; i < 50 && browser.found().empty(); ++i) {
        host.poll(now);
        browser.poll(now);
        now += 20;
        std::this_thread::sleep_for(std::chrono::milliseconds(20));
    }
    check(browser.found().size() == 1 && browser.found()[0].row == R"({"rs_name":"Dad's park","rs_code":"x"})" &&
              browser.found()[0].from == loopback,
          "the browser finds the host's game");

    // A second search starts again; a host that stopped hosting is not found.
    host.announce({});
    check(!host.announcing(), "the host stops");
    browser.search(loopback);
    for (int i = 0; i < 10; ++i) {
        host.poll(now);
        browser.poll(now);
        std::this_thread::sleep_for(std::chrono::milliseconds(20));
    }
    check(browser.found().empty(), "nothing is found once the host stops");

    // A listing too long to send is not announced.
    host.announce(std::string(LanDiscovery::max_row + 1, 'x'));
    check(!host.announcing(), "an oversized listing is refused");

    std::printf("%s\n", failures ? "FAILED" : "LAN discovery: private ranges, finding a host, stopping passed.");
    return failures ? 1 : 0;
}
