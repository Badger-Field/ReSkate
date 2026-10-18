#include "gui_internal.h"

#include "text_encoding.h"

#include "Engine/Core/Log/logging.h"

#include "legal_documents.h"

#include <algorithm>
#include <format>
#include <string_view>

// The LEGAL panel: this edition's terms of use and privacy policy, built into the launcher from
// legal/*.md (cmake/Apps.cmake), so what a player agrees to is what the release they run ships.
namespace dingosdk::launcher_gui::detail {
namespace {
constexpr const char* names[Legal::count]{"PRIVACY POLICY", "TERMS OF USE"};

Legal::Document built_in(int kind) {
    if (kind == Legal::privacy)
        return {legal_privacy_title, legal_privacy_date, legal_privacy_markdown, legal_privacy_version};
    return {legal_terms_title, legal_terms_date, legal_terms_markdown, legal_terms_version};
}

void load(Legal& legal, int kind) {
    auto& slot = legal.slots[static_cast<std::size_t>(kind)];
    slot.document = built_in(kind);
    // The panel shows the title and version above the text: the body starts after the file's own
    // "# Title" and "Version N, YYYY-MM-DD" lines (cmake/Apps.cmake checks they are there).
    std::string_view body = slot.document.markdown;
    if (const auto version = body.find("\nVersion "); version != std::string_view::npos)
        if (const auto end = body.find('\n', version + 1); end != std::string_view::npos) body.remove_prefix(end + 1);
    while (body.starts_with('\n')) body.remove_prefix(1);
    slot.text = thunderstore::readme_lines(body, 2000);
    slot.loaded = true;
}
} // namespace

void legal_window(const Fonts& fonts, ImVec2 size, Ui& ui) {
    auto& legal = ui.legal;
    const int kind = legal.open;
    auto& slot = legal.slots[static_cast<std::size_t>(kind)];
    if (!slot.loaded) load(legal, kind);

    const auto panel = begin_panel("##legal_panel", size, ImVec2(S(880), S(680)));
    panel_title(fonts, names[kind]);
    const float footer = ImGui::GetFrameHeight() + S(24) + S(12);
    ImGui::BeginChild("##legal_body", ImVec2(0, panel.y - ImGui::GetCursorPosY() - footer), ImGuiChildFlags_NavFlattened);
    {
        const auto& document = slot.document;
        ImGui::PushFont(fonts.heading);
        ImGui::TextUnformatted(document.title.c_str());
        ImGui::PopFont();
        ImGui::TextDisabled("Version %llu, updated %s", static_cast<unsigned long long>(document.version), document.updated.c_str());
        ImGui::Dummy(ImVec2(0, S(6)));
        draw_readme(fonts, slot.text);
    }
    ImGui::EndChild();

    ImGui::SetCursorPosY(panel.y - S(24) - ImGui::GetFrameHeight());
    const int other = kind == Legal::privacy ? Legal::terms : Legal::privacy;
    if (ImGui::Button(kind == Legal::privacy ? "Terms of Use" : "Privacy Policy", ImVec2(S(180), 0))) legal.open = other;
    ImGui::SameLine();
    ImGui::SetCursorPosX(panel.x - S(28) - S(110));
    const bool close = ImGui::Button("CLOSE", ImVec2(S(110), 0));
    default_focus();
    if (close || ImGui::IsKeyPressed(ImGuiKey_Escape, false)) legal.open = -1;
    ImGui::End();
}

void check_legal_versions(Ui& ui, const Settings& settings) {
    auto& legal = ui.legal;
    if (!legal.versions_started) {
        legal.versions_started = true;
        std::lock_guard lock(legal.mutex);
        legal.current = {legal_privacy_version, legal_terms_version};
        legal.versions_arrived = true;
    }
    std::lock_guard lock(legal.mutex);
    if (!legal.versions_arrived) return;
    legal.versions_arrived = false;
    const auto& current = legal.current;
    legal.agreement = current[Legal::privacy] > settings.accepted_privacy || current[Legal::terms] > settings.accepted_terms;
    if (legal.agreement)
        logging::write(logging::Level::info, logging::Channel::launcher,
                       std::format("Privacy policy {} and terms {} to agree to (agreed: {} and {}).", current[Legal::privacy],
                                   current[Legal::terms], settings.accepted_privacy, settings.accepted_terms));
}

void legal_agreement_window(Launcher& launcher, const Fonts& fonts, ImVec2 size, Ui& ui, HWND window) {
    auto& legal = ui.legal;
    auto& settings = launcher.settings();
    const bool first = !settings.accepted_privacy && !settings.accepted_terms;
    const auto panel = begin_panel("##legal_agreement", size, ImVec2(S(640), S(470)));
    panel_title(fonts, first ? "BEFORE YOU PLAY" : "UPDATED TERMS");
    ImGui::PushTextWrapPos(0);
    if (first) {
        ImGui::TextUnformatted("This edition of ReSkate has terms of use and a privacy policy. Read them, and agree to keep using it.");
    } else {
        std::string changed;
        if (legal.current[Legal::terms] > settings.accepted_terms) changed = "terms of use";
        if (legal.current[Legal::privacy] > settings.accepted_privacy)
            changed += changed.empty() ? "privacy policy" : " and the privacy policy";
        ImGui::TextUnformatted(std::format("The {} changed since you last agreed. Read what changed, and agree to keep using "
                                           "ReSkate.", changed).c_str());
    }
    ImGui::Dummy(ImVec2(0, S(8)));
    ImGui::TextDisabled("In short:");
    // (BulletText does not wrap: a bullet, then wrapped text beside it.)
    for (const char* line : {"Play fair online and respect other players.",
                             "Don't unlock or copy cosmetics that skate sells.",
                             "No telemetry and no crash uploads: we only see the bug reports you choose to send.",
                             "Levels and data made with ReSkate's real-place tools are public places only, and ours to "
                             "publish, license or sell.",
                             "A free fork by Badger Field, not the original ReSkate team or EA, with no warranty."}) {
        ImGui::Bullet();
        ImGui::TextWrapped("%s", line);
    }
    ImGui::PopTextWrapPos();
    ImGui::Dummy(ImVec2(0, S(10)));
    if (ImGui::Button("Read the Terms of Use", ImVec2(S(260), 0))) legal.open = Legal::terms;
    ImGui::SameLine();
    if (ImGui::Button("Read the Privacy Policy", ImVec2(S(260), 0))) legal.open = Legal::privacy;

    ImGui::SetCursorPosY(panel.y - S(24) - ImGui::GetFrameHeight());
    if (ImGui::Button("QUIT", ImVec2(S(110), 0))) PostMessageW(window, WM_CLOSE, 0, 0);
    ImGui::SameLine();
    ImGui::SetCursorPosX(panel.x - S(28) - S(160));
    push_primary_button();
    const bool agree = ImGui::Button("I AGREE", ImVec2(S(160), 0));
    pop_primary_button();
    default_focus();
    if (agree) {
        settings.accepted_privacy = (std::max)(settings.accepted_privacy, legal.current[Legal::privacy]);
        settings.accepted_terms = (std::max)(settings.accepted_terms, legal.current[Legal::terms]);
        launcher.save();
        legal.agreement = false;
        logging::write(logging::Level::info, logging::Channel::launcher,
                       std::format("Agreed to privacy policy {} and terms {}.", settings.accepted_privacy, settings.accepted_terms));
    }
    ImGui::End();
}

} // namespace dingosdk::launcher_gui::detail
