// 用 Node 执行生成的 Script.js：node run_script.js <Script.js> <订阅.json>，输出处理后的配置 JSON。
// 只用于离线测试，模拟 Clash Verge 调用 main(config, profileName)。
"use strict";
const fs = require("fs");
const vm = require("vm");
const [scriptPath, configPath] = process.argv.slice(2);
const sandbox = {};
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(scriptPath, "utf8") + "\nthis.__main = main;", sandbox);
const input = JSON.parse(fs.readFileSync(configPath, "utf8"));
const output = sandbox.__main(input, "test-profile");
process.stdout.write(JSON.stringify(output));
