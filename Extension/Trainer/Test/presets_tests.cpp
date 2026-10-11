// The REALISTIC list's presets (trainer_presets.h): that Hardcore goes at least as far as
// Realistic on everything Realistic changes and shows on that list, and that each of their rules
// naming one of the game's data-defined classes names a field it has. A pattern that matches
// nothing is skipped without a word in game, so a misspelt one would otherwise go unnoticed.
// Only these two presets are checked: in the others a pattern such as "wipeout.x" may be meant
// for the physics tuning asset ("physicswipeout.x"), whose names need the game's data.
#include "Extension/Trainer/trainer_classes.h"
#include "Extension/Trainer/trainer_presets.h"

#include <algorithm>
#include <cctype>
#include <iostream>
#include <string>
#include <string_view>

namespace {
using namespace dingosdk::trainer;
int failures = 0;
void check(bool condition, const std::string &message) {
    if (!condition) { std::cerr << "FAIL: " << message << '\n'; ++failures; }
}
std::string lower(std::string_view text) {
    std::string out(text);
    for (auto &c : out) c = static_cast<char>(std::tolower(static_cast<unsigned char>(c)));
    return out;
}
const BuiltinPreset *find_preset(std::string_view name) {
    const auto &presets = builtin_presets();
    const auto found = std::ranges::find(presets, name, &BuiltinPreset::name);
    return found == presets.end() ? nullptr : &*found;
}
// The class whose key the pattern starts with ("push." for "push.maxpushspeedlight"), if any.
const ClassSpec *class_of(std::string_view pattern) {
    for (const auto &spec : class_specs)
        if (const auto key = lower(spec.key); pattern.size() > key.size() && pattern.starts_with(key) && pattern[key.size()] == '.')
            return &spec;
    return nullptr;
}
}

int main() {
    const auto *realistic = find_preset("Realistic");
    const auto *hardcore = find_preset("Hardcore");
    check(realistic != nullptr, "Realistic is a built-in preset");
    check(hardcore != nullptr, "Hardcore is a built-in preset");
    if (realistic && hardcore) {
        check((preset_dial("Hardcore").modes & mode_realistic) != 0, "Hardcore shows on the REALISTIC list");
        // Every value Realistic turns down, Hardcore turns down at least as far, so switching
        // from one to the other never makes the game easier.
        for (const auto &rule : realistic->rules) {
            const auto same = std::ranges::find(hardcore->rules, rule.pattern, &PresetRule::pattern);
            check(same != hardcore->rules.end(), "Hardcore also sets \"" + std::string(rule.pattern) + "\"");
            if (same == hardcore->rules.end()) continue;
            check(same->multiply == rule.multiply && same->curves == rule.curves && same->amount <= rule.amount,
                  "Hardcore goes at least as far as Realistic on \"" + std::string(rule.pattern) + "\"");
        }
    }
    for (const auto *preset : {realistic, hardcore}) {
        if (!preset) continue;
        for (const auto &rule : preset->rules) {
            if (rule.multiply) check(rule.amount > 0, std::string(preset->name) + ": a multiplier above 0 for \"" + std::string(rule.pattern) + "\"");
            // One word that starts with a class's key: it is meant for that class's fields.
            if (rule.curves || rule.pattern.find(' ') != std::string_view::npos) continue;
            const auto *spec = class_of(rule.pattern);
            if (!spec) continue;
            bool found = false;
            for (std::size_t i = 0; i < spec->count && !found; ++i)
                found = lower(std::string(spec->key) + "." + class_fields[spec->first + i].name).find(rule.pattern) != std::string::npos;
            check(found, std::string(preset->name) + ": \"" + std::string(rule.pattern) + "\" names a field of " + spec->key);
        }
    }
    if (failures) {
        std::cerr << failures << " check(s) failed.\n";
        return 1;
    }
    std::cout << "Trainer presets: all checks passed.\n";
    return 0;
}
