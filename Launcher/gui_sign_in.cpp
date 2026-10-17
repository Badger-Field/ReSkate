#include "gui_internal.h"

#include <cmath>
#include <cstdint>
#include <span>

// Getting the game (from this PC, or from Steam), and the Steam panels: sign-in, password/Steam
// Guard prompts and the QR code.
namespace dingosdk::launcher_gui::detail {
namespace {

void wipe(std::span<char> buffer) { SecureZeroMemory(buffer.data(), buffer.size()); }

} // namespace

void open_sign_in(Launcher& launcher, Ui& ui, bool validate) {
    ui.sign_in = true;
    ui.sign_in_validate = validate;
    ui.focus = true;
    wipe(ui.password);
    ui.username.fill(0);
    const auto& saved = launcher.settings().steam_username;
    std::copy_n(saved.begin(), std::min(saved.size(), ui.username.size() - 1), ui.username.begin());
}

void sign_in_window(Launcher& launcher, const Fonts& fonts, ImVec2 size, Ui& ui) {
    const auto panel = begin_panel("##sign_in_panel", size, ImVec2(S(480), S(520)));
    auto& settings = launcher.settings();
    panel_title(fonts, "SIGN IN TO STEAM");
    ImGui::PushTextWrapPos(0);
    ImGui::TextDisabled("skate. is downloaded from Steam with DepotDownloader, so the account must own it. "
                        "Your password is passed straight to Steam and never saved.");
    ImGui::PopTextWrapPos();
    ImGui::Spacing();
    const auto start = [&](bool qr) {
        if (!qr) settings.steam_username = ui.username.data();
        launcher.save();
        launcher.download(ui.sign_in_validate, qr, qr ? std::string() : std::string(ui.password.data()));
        wipe(ui.password);
        ui.sign_in = false;
    };
    push_primary_button();
    const bool qr = ImGui::Button("SCAN A QR CODE WITH THE STEAM APP", ImVec2(-1, S(46)));
    pop_primary_button();
    if (qr) start(true);
    ImGui::Spacing();
    ImGui::PushFont(fonts.caption);
    ImGui::TextDisabled("OR SIGN IN WITH YOUR ACCOUNT");
    ImGui::PopFont();
    if (ui.focus) { ImGui::SetKeyboardFocusHere(); ui.focus = false; }
    ImGui::SetNextItemWidth(-1);
    ImGui::InputTextWithHint("##username", "Steam account name", ui.username.data(), ui.username.size(),
        ImGuiInputTextFlags_CharsNoBlank);
    ImGui::SetNextItemWidth(-1);
    const bool entered = ImGui::InputTextWithHint("##password", "Password (empty if remembered)", ui.password.data(),
        ui.password.size(), ImGuiInputTextFlags_Password | ImGuiInputTextFlags_EnterReturnsTrue);
    ImGui::Checkbox("Remember me on this PC", &settings.steam_remember);
    ImGui::Checkbox("Type a Steam Guard code instead of approving in the app", &settings.steam_prefer_code);
    const std::string_view username(ui.username.data());
    const bool valid = !username.empty() && username.find_first_not_of(
        "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_") == std::string_view::npos;
    ImGui::SetCursorPosY(panel.y - S(24) - ImGui::GetFrameHeight());
    if (ImGui::Button("CANCEL", ImVec2(S(110), 0))) { wipe(ui.password); ui.sign_in = false; }
    ImGui::SameLine(panel.x - S(28) - S(130));
    ImGui::BeginDisabled(!valid);
    push_primary_button();
    const bool sign_in = ImGui::Button("SIGN IN", ImVec2(S(130), 0));
    pop_primary_button();
    if (sign_in || (entered && valid)) start(false);
    ImGui::EndDisabled();
    ImGui::End();
}

// GET THE GAME: copy skate. from this PC (the default, when the supported build is installed here),
// a folder the player picks, or the Steam download with an account.
void get_game_window(Launcher& launcher, const Fonts& fonts, ImVec2 size, Ui& ui, const State& state, HWND window) {
    const auto panel = begin_panel("##get_game_panel", size, ImVec2(S(520), S(400)));
    panel_title(fonts, "GET THE GAME");
    const auto* local = !state.local_games.empty() && state.local_games.front().supported ? &state.local_games.front() : nullptr;
    ImGui::PushTextWrapPos(0);
    if (local) {
        ImGui::TextUnformatted("skate. is installed on this PC, as the build ReSkate supports:");
        ImGui::TextDisabled("%s", launcher_text::utf8(local->folder.wstring()).c_str());
        ImGui::TextDisabled("ReSkate copies it into its own folder, so a Steam update cannot change ReSkate's game. "
                            "Nothing is downloaded, Steam is not opened, and the game there is left as it is.");
    } else {
        ImGui::TextUnformatted("Choose the folder skate. is installed in (the one with Skate.exe), and ReSkate copies "
                               "it into its own folder. It has to be the build ReSkate supports.");
        if (!state.local_games.empty())
            ImGui::TextDisabled("The skate. in %s is another build, so it cannot be copied.",
                                launcher_text::utf8(state.local_games.front().folder.wstring()).c_str());
    }
    ImGui::PopTextWrapPos();
    ImGui::Spacing();
    if (local) {
        push_primary_button();
        const bool copy = ImGui::Button("COPY GAME FROM THIS PC", ImVec2(-1, S(46)));
        pop_primary_button();
        if (copy) {
            ui.get_game = false;
            launcher.copy_game(local->folder);
        }
    }
    if (!local) push_primary_button();
    const bool choose = ImGui::Button("CHOOSE GAME FOLDER...", ImVec2(-1, S(local ? 36.0f : 46.0f)));
    if (!local) pop_primary_button();
    if (choose) {
        const auto folder = pick_path(window, true, L"Choose your skate. folder (the one with Skate.exe)", nullptr, nullptr);
        if (!folder.empty()) {
            ui.get_game = false;
            launcher.copy_game(folder);
        }
    }
    ImGui::Spacing();
    ImGui::PushFont(fonts.caption);
    ImGui::TextDisabled("OR");
    ImGui::PopFont();
    if (ImGui::Button("DOWNLOAD WITH A STEAM ACCOUNT", ImVec2(-1, S(36)))) {
        ui.get_game = false;
        open_sign_in(launcher, ui, false);
    }
    ImGui::SetCursorPosY(panel.y - S(24) - ImGui::GetFrameHeight());
    if (ImGui::Button("CANCEL", ImVec2(S(110), 0)) || ImGui::IsKeyPressed(ImGuiKey_Escape, false)) ui.get_game = false;
    ImGui::End();
}

void prompt_window(Launcher& launcher, const Fonts& fonts, ImVec2 size, const update::Prompt& prompt, Ui& ui) {
    const auto panel = begin_panel("##prompt_panel", size, ImVec2(S(440), S(300)));
    const bool password = prompt.kind == update::PromptKind::password;
    panel_title(fonts, password ? "STEAM PASSWORD" : "STEAM GUARD");
    ImGui::PushTextWrapPos(0);
    if (prompt.retry) ImGui::TextColored(ImGui::ColorConvertU32ToFloat4(color::danger), "That code was incorrect. Try again.");
    switch (prompt.kind) {
    case update::PromptKind::password:
        ImGui::TextDisabled("Enter the password for %s.", launcher.settings().steam_username.c_str());
        break;
    case update::PromptKind::authenticator_code:
        ImGui::TextDisabled("Enter the code shown in the Steam app on your phone (Steam Guard).");
        break;
    case update::PromptKind::email_code: {
        const auto at = prompt.text.find("email at ");
        ImGui::TextDisabled("Enter the code Steam emailed to %s.",
            at == std::string::npos ? "you" : prompt.text.c_str() + at + 9);
        break;
    }
    }
    ImGui::PopTextWrapPos();
    ImGui::Spacing();
    if (ImGui::IsWindowAppearing()) ImGui::SetKeyboardFocusHere();
    ImGui::SetNextItemWidth(-1);
    bool submit = false;
    if (password) {
        submit = ImGui::InputText("##prompt_password", ui.password.data(), ui.password.size(),
            ImGuiInputTextFlags_Password | ImGuiInputTextFlags_EnterReturnsTrue);
    } else {
        ImGui::PushFont(fonts.heading);
        submit = ImGui::InputTextWithHint("##prompt_code", "XXXXX", ui.code.data(), ui.code.size(),
            ImGuiInputTextFlags_CharsUppercase | ImGuiInputTextFlags_CharsNoBlank | ImGuiInputTextFlags_EnterReturnsTrue);
        ImGui::PopFont();
    }
    const char* value = password ? ui.password.data() : ui.code.data();
    ImGui::SetCursorPosY(panel.y - S(24) - ImGui::GetFrameHeight());
    if (ImGui::Button("CANCEL", ImVec2(S(110), 0))) {
        wipe(ui.password);
        ui.code.fill(0);
        launcher.answer(std::nullopt);
    }
    ImGui::SameLine(panel.x - S(28) - S(130));
    ImGui::BeginDisabled(!*value);
    push_primary_button();
    const bool proceed = ImGui::Button("CONTINUE", ImVec2(S(130), 0));
    pop_primary_button();
    if ((proceed || submit) && *value) {
        launcher.answer(std::string(value));
        wipe(ui.password);
        ui.code.fill(0);
    }
    ImGui::EndDisabled();
    ImGui::End();
}

void qr_window(Launcher& launcher, const Fonts& fonts, ImVec2 size, const std::vector<std::string>& rows) {
    std::size_t first = SIZE_MAX, last = 0;
    for (const auto& row : rows) {
        const auto begin = row.find('#'), end = row.rfind('#');
        if (begin != std::string::npos) { first = std::min(first, begin); last = std::max(last, end); }
    }
    if (first == SIZE_MAX) return;
    const float columns = static_cast<float>(last - first + 1);
    const float module = std::floor(S(230) / columns);
    const float code = module * columns;
    const float quiet = module * 3;
    const auto panel = begin_panel("##qr_panel", size, ImVec2(S(440), code + quiet * 2 + S(200)));
    panel_title(fonts, "SCAN WITH STEAM");
    ImGui::PushTextWrapPos(0);
    ImGui::TextDisabled("Open the Steam app on your phone, tap the shield (Steam Guard) and scan this code. "
                        "The account must own skate.");
    ImGui::PopTextWrapPos();
    auto* draw = ImGui::GetWindowDrawList();
    const auto window = ImGui::GetWindowPos();
    const ImVec2 origin(window.x + (panel.x - code - quiet * 2) * 0.5f, ImGui::GetCursorScreenPos().y + S(8));
    draw->AddRectFilled(origin, ImVec2(origin.x + code + quiet * 2, origin.y + module * static_cast<float>(rows.size()) + quiet * 2),
        rgba(255, 255, 255));
    for (std::size_t y = 0; y < rows.size(); ++y)
        for (std::size_t x = first; x <= last && x < rows[y].size(); ++x)
            if (rows[y][x] == '#') {
                const ImVec2 cell(origin.x + quiet + static_cast<float>(x - first) * module,
                                  origin.y + quiet + static_cast<float>(y) * module);
                draw->AddRectFilled(cell, ImVec2(cell.x + module, cell.y + module), rgba(0, 0, 0));
            }
    ImGui::SetCursorPosY(panel.y - S(24) - ImGui::GetFrameHeight());
    ImGui::SetCursorPosX(panel.x - S(28) - S(110));
    if (ImGui::Button("CANCEL", ImVec2(S(110), 0))) launcher.cancel();
    ImGui::End();
}

} // namespace dingosdk::launcher_gui::detail
