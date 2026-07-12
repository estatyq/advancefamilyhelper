--[[
    Family Screener for GTA SA-MP (Advance RP)
    Author: Antigravity
    Description: Автоматический сбор очков участников семьи и учет выплат.
]]

local sampev = require 'lib.samp.events'
local json = require 'json'

-- Configuration paths
local config_dir = getWorkingDirectory() .. "/config"
local config_filepath = config_dir .. "/family_screener_config.json"

-- Default settings
local config = {
    server_url = "http://localhost:5000",
    secret_token = "ChangeMeSuperSecretToken123!",
    auto_scan_after_20h = true,
    last_scan_date = ""
}

local scanning = false
local scan_start_time = 0

-- Load local configuration
local function saveConfig()
    local file = io.open(config_filepath, "w")
    if file then
        file:write(json.encode(config))
        file:close()
    end
end

local function loadConfig()
    -- Create directory if it doesn't exist
    if not doesDirectoryExist(config_dir) then
        createDirectory(config_dir)
    end
    
    local file = io.open(config_filepath, "r")
    if file then
        local content = file:read("*a")
        file:close()
        local ok, parsed = pcall(json.decode, content)
        if ok and parsed then
            config.server_url = parsed.server_url or config.server_url
            config.secret_token = parsed.secret_token or config.secret_token
            if parsed.auto_scan_after_20h ~= nil then
                config.auto_scan_after_20h = parsed.auto_scan_after_20h
            end
            config.last_scan_date = parsed.last_scan_date or config.last_scan_date
        end
    else
        saveConfig()
    end
end

local function getMyName()
    if not isSampAvailable() then return "Unknown" end
    local _, id = sampGetPlayerIdByCharHandle(PLAYER_PED)
    if id then
        local name = sampGetPlayerNickname(id)
        if name and name ~= "" then return name end
    end
    return "Unknown"
end

local function parseDialogText(text)
    local members = {}
    for line in text:gmatch("[^\r\n]+") do
        -- Skip column headers if any
        if not line:find("Участник") and not line:find("Ранг") and not line:find("Заработано очков") then
            -- Columns are tab-separated (\t) in DIALOG_STYLE_TABLIST_HEADERS
            local cols = {}
            for col in line:gmatch("[^\t]+") do
                table.insert(cols, col)
            end
            
            if #cols >= 3 then
                local raw_name = cols[1]
                local rank = cols[2]
                local raw_points = cols[3]
                
                -- Clean nickname from online/offline tags (e.g. "Name (онлайн)")
                local name = raw_name:gsub("%s*%(онлайн%)", ""):gsub("%s*%(оффлайн%)", "")
                name = name:match("^%s*(.-)%s*$") -- trim whitespace
                
                -- Extract points (e.g. "10 / 1143")
                local points_day, points_total = raw_points:match("(%d+)%s*/%s*(%d+)")
                
                if name and rank and points_day and points_total then
                    table.insert(members, {
                        name = name,
                        rank = rank:match("^%s*(.-)%s*$"),
                        points_day = tonumber(points_day),
                        points_total = tonumber(points_total)
                    })
                end
            end
        end
    end
    return members
end

local function saveAndSendScan(members)
    local data = {
        scanner_name = getMyName(),
        members = members
    }
    
    local filepath = config_dir .. "/family_scan.json"
    local file = io.open(filepath, "w")
    if file then
        file:write(json.encode(data))
        file:close()
        
        -- Send POST request asynchronously using start /b curl on Windows
        local url = config.server_url .. "/api/scan"
        local cmd = string.format('start /b curl -s -X POST -H "Content-Type: application/json" -H "Authorization: Bearer %s" -d @%s "%s"', config.secret_token, filepath, url)
        os.execute(cmd)
        sampAddChatMessage("[FamilyScreener] Данные очков успешно отправлены на сервер!", 0x2ECC71)
    else
        sampAddChatMessage("[FamilyScreener] Ошибка: не удалось сохранить файл сканирования.", 0xE74C3C)
    end
end

local function sendGamePay(name, amount)
    local data = {
        sender_name = getMyName(),
        player_name = name,
        amount = amount
    }
    
    local filepath = config_dir .. "/family_pay.json"
    local file = io.open(filepath, "w")
    if file then
        file:write(json.encode(data))
        file:close()
        
        -- Send game payment notification to backend
        local url = config.server_url .. "/api/game-pay"
        local cmd = string.format('start /b curl -s -X POST -H "Content-Type: application/json" -H "Authorization: Bearer %s" -d @%s "%s"', config.secret_token, filepath, url)
        os.execute(cmd)
        sampAddChatMessage(string.format("[FamilyScreener] Выплата игроку %s на сумму %d$ отправлена на учет.", name, amount), 0xF1C40F)
    end
end

-- Hook outgoing chat commands to catch in-game payments (/pay or /transfer)
function sampev.onSendCommand(command)
    -- Match command format e.g. /pay [id/nick] [amount] or /transfer [id/nick] [amount]
    local cmd, arg1, arg2 = command:match("^/(%a+)%s+(%S+)%s+(%d+)")
    if (cmd == "pay" or cmd == "transfer") and arg1 and arg2 then
        local amount = tonumber(arg2)
        local name = ""
        local id = tonumber(arg1)
        
        if id then
            name = sampGetPlayerNickname(id)
        else
            name = arg1
        end
        
        if name and name ~= "" and amount then
            sendGamePay(name, amount)
        end
    end
end

-- Hook dialog show event
function sampev.onShowDialog(dialogId, style, title, button1, button2, text)
    if scanning then
        -- 1. Family Menu (Select "Участники семьи")
        if title:find("Управление семьей") then
            local index = 0
            local found = false
            for line in text:gmatch("[^\r\n]+") do
                if line:find("Участники семьи") then
                    found = true
                    break
                end
                index = index + 1
            end
            
            if found then
                sampSendDialogResponse(dialogId, 1, index, "")
            else
                scanning = false
                sampAddChatMessage("[FamilyScreener] Ошибка: Пункт 'Участники семьи' не найден.", 0xE74C3C)
            end
            return false -- Hide dialog from player
        end
        
        -- 2. Members List Dialog (Scan points and close)
        if title:find("Список игроков в семье") then
            local members = parseDialogText(text)
            if #members > 0 then
                saveAndSendScan(members)
                config.last_scan_date = os.date("%Y-%m-%d")
                saveConfig()
            else
                sampAddChatMessage("[FamilyScreener] Ошибка: Не удалось распознать участников.", 0xE74C3C)
            end
            
            -- Simulate clicking button2 ("Назад" / index 0)
            sampSendDialogResponse(dialogId, 0, 0, "")
            scanning = false
            return false -- Hide dialog from player
        end
    end
end

function main()
    if not isSampLoaded() or not isSampfuncsLoaded() then return end
    while not isSampAvailable() do wait(100) end
    
    loadConfig()
    
    sampRegisterChatCommand("fscan", function()
        scanning = true
        scan_start_time = os.clock()
        sampSendChat("/family")
        sampAddChatMessage("[FamilyScreener] Запущен сбор очков семьи...", 0x3498DB)
    end)
    
    -- Main background thread loop
    while true do
        wait(10000) -- Check every 10 seconds
        
        -- 1. Scan Timeout Check
        if scanning and (os.clock() - scan_start_time > 10) then
            scanning = false
            sampAddChatMessage("[FamilyScreener] Ошибка: Превышено время ожидания меню семьи.", 0xE74C3C)
        end
        
        -- 2. Automatic Evening Scan Trigger (After 20:00)
        if config.auto_scan_after_20h and not scanning then
            local today = os.date("%Y-%m-%d")
            if config.last_scan_date ~= today then
                local time_table = os.date("*t")
                if time_table.hour >= 20 then
                    scanning = true
                    scan_start_time = os.clock()
                    sampSendChat("/family")
                    sampAddChatMessage("[FamilyScreener] Запуск автоматического вечернего скрининга...", 0x3498DB)
                end
            end
        end
    end
end
