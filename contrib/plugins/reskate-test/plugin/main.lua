-- reskate-test: calls every plugin API and says what worked. Run `ptest all` in the console, or
-- `ptest <module>` for one (`ptest list` names them). Each API ReSkate adds gets a module here.
local harness = require("harness")

for _, name in {"base", "time", "level", "package", "json", "require", "console", "storage", "sandbox"} do
    require("checks/" .. name)
end

reskate.console.command("ptest", "Checks the plugin API: ptest all, ptest <module>, ptest list", function(args)
    local what = args[1] or "all"
    if what == "list" then
        reskate.console.print("ptest modules: " .. table.concat(harness.names(), ", "))
    elseif what == "all" then
        harness.run(harness.names())
    elseif harness.has(what) then
        harness.run({what})
    else
        reskate.console.print("ptest: there is no module " .. what .. " (ptest list names them)")
    end
end)

reskate.log("reskate-test loaded: ptest all checks every plugin API")
