// netparse CMDB 第二档：自动补全 + lint 实时诊断 + 定义跳转
const vscode = require('vscode');
const { execFile } = require('child_process');
const path = require('path');
const fs = require('fs');

// ── 诊断集合 ──
const diagColl = vscode.languages.createDiagnosticCollection('netparse');
let outChannel = null;
let activeSelftestDoc = null;   // 最近一次点 CodeLens 的文档

function activate(ctx) {
    // ── 1. 自动补全（段头感知）──
    ctx.subscriptions.push(vscode.languages.registerCompletionItemProvider(
        'cmdb', new CmdbCompletionProvider(), '.', '{', ' '));

    // ── 2. lint 实时诊断（保存/打开时跑 netparse lint --json）──
    ctx.subscriptions.push(diagColl);
    ctx.subscriptions.push(vscode.workspace.onDidSaveTextDocument(doc => maybeLint(doc)));
    ctx.subscriptions.push(vscode.workspace.onDidOpenTextDocument(doc => maybeLint(doc)));
    // 初始打开的文件
    vscode.workspace.textDocuments.forEach(maybeLint);

    // ── 3. 定义跳转：${Field} → 字段声明行；import x → 该文件 ──
    ctx.subscriptions.push(vscode.languages.registerDefinitionProvider(
        'cmdb', new CmdbDefinitionProvider()));

    // ── 4. [__main__] CodeLens：▶ 运行自测试 ──
    ctx.subscriptions.push(vscode.languages.registerCodeLensProvider(
        'cmdb', new CmdbCodeLensProvider()));
    outChannel = vscode.window.createOutputChannel('netparse [__main__]');
    ctx.subscriptions.push(outChannel);
    ctx.subscriptions.push(vscode.commands.registerCommand(
        'netparse.runSelftest', () => runSelftest()));
    ctx.subscriptions.push(vscode.commands.registerCommand(
        'netparse.runTestSection', (uri, id) => runTestSection(uri, id)));
    ctx.subscriptions.push(vscode.commands.registerCommand(
        'netparse.exportSelftestJson', exportSelftestJson));
}

function deactivate() {}

// ═══ 自动补全 ═══
class CmdbCompletionProvider {
    provideCompletionItems(doc, pos) {
        const lineText = doc.lineAt(pos.line).text;
        const before = lineText.slice(0, pos.character);
        const items = [];

        // import/from → 目录里的 .cmdb 文件名
        if (/^\s*(import\s+\S*|from\s+\S*)$/.test(before) || /from\s+\S*$/.test(before)) {
            const dir = path.dirname(doc.uri.fsPath);
            for (const f of fs.readdirSync(dir)) {
                if (f.endsWith('.cmdb')) {
                    const name = f.replace(/\.cmdb$/, '');
                    items.push(item(name, '组模板', vscode.CompletionItemKind.Module));
                    items.push(item(f, '模板文件', vscode.CompletionItemKind.File));
                }
            }
            return items;
        }

        // [value] 段字段声明 → type(...) / option(Key) / func=
        if (/^\s+\w+\s+(type\([^)]*\))?\s*\w*$/.test(before) || /\btype$/.test(before)) {
            for (const t of ['type(list)', 'type(dict)', 'type(int)', 'type(str)',
                             'type(bool)', 'type(secret)']) {
                items.push(item(t, '字段类型', vscode.CompletionItemKind.Keyword));
            }
        }
        if (/option$/.test(before) || /\stype\([^)]*\)\s*$/.test(before)) {
            items.push(item('option(Key)', '键字段（同键合并）', vscode.CompletionItemKind.Keyword));
            items.push(item('option(Required)', '必填字段', vscode.CompletionItemKind.Keyword));
        }
        if (/func=$/.test(before)) {
            // 补全本模板 [python_file] 引用的 py 里的函数名
            for (const py of pyRefs(doc)) {
                const full = path.resolve(path.dirname(doc.uri.fsPath), py);
                try {
                    const src = fs.readFileSync(full, 'utf-8');
                    for (const m of src.matchAll(/^def\s+(\w+)\s*\(/gm)) {
                        items.push(item(m[1], 'transform 函数（' + py + '）',
                                        vscode.CompletionItemKind.Function));
                    }
                } catch (e) { /* 文件缺失则跳过 */ }
            }
        }

        // [rule] 段 → ${已声明字段} 补全
        const fields = declaredFields(doc);
        if (/^\s+\^/.test(lineText) || /\$\{?$/.test(before)) {
            for (const [name, p] of fields) {
                items.push(item('${' + name + '}', '字段引用 → ' + p,
                                vscode.CompletionItemKind.Variable));
            }
            // target 引用赋值提示
            if (/->\s*\w+\{[^}]*$/.test(before) && !/=$/.test(before)) {
                for (const [name, p] of fields) {
                    items.push(item(name + '=' + p, '引用赋值（键=捕获路径）',
                                    vscode.CompletionItemKind.Property));
                }
            }
        }

        // -> 行 → 补集合名 / In / Out
        if (/^\s*->\s*\w*$/.test(before)) {
            for (const coll of declaredCollections(doc)) {
                items.push(item(coll, '集合 target', vscode.CompletionItemKind.Class));
            }
            items.push(item('In', '入方向视图', vscode.CompletionItemKind.Class));
            items.push(item('Out', '出方向视图', vscode.CompletionItemKind.Class));
        }

        // 段头补全
        if (/^\s*\[$/.test(before) || /^\s*$/.test(lineText)) {
            for (const sec of ['[value]', '[rule]', '[python]', '[python_file]']) {
                items.push(item(sec, '段头', vscode.CompletionItemKind.Keyword));
            }
        }
        return items;
    }
}

// ═══ 定义跳转 ═══
class CmdbDefinitionProvider {
    provideDefinition(doc, pos) {
        const range = doc.getWordRangeAtPosition(pos, /\$\{?[A-Za-z_][\w.]*\}?|import\s+\S+|from\s+\S+/);
        if (!range) return null;
        const word = doc.getText(range);
        const results = [];

        // ${Field} → 字段声明行
        const fieldName = word.replace(/[${}]/g, '').split('.').pop();
        for (let i = 0; i < doc.lineCount; i++) {
            const m = doc.lineAt(i).text.match(new RegExp('^(\\s+|)' + escape(fieldName) + '\\s+type\\('));
            if (m) results.push(new vscode.Location(doc.uri, new vscode.Position(i, 0)));
        }
        // import/from x → x 文件
        const mImp = word.match(/(?:import|from)\s+(\S+)/);
        if (mImp) {
            let rel = mImp[1];
            if (!path.extname(rel)) rel += '.cmdb';
            const full = path.resolve(path.dirname(doc.uri.fsPath), rel);
            if (fs.existsSync(full)) results.push(vscode.Uri.file(full));
        }
        return results.length ? results : null;
    }
}

// ═══ lint 诊断 ═══
let lintTimer = null;
function maybeLint(doc) {
    if (doc.languageId !== 'cmdb' || doc.uri.scheme !== 'file') return;
    clearTimeout(lintTimer);
    lintTimer = setTimeout(() => lintDoc(doc), 600);   // 防抖 600ms
}

function lintDoc(doc) {
    const bin = getExecPath();
    const cwd = path.dirname(doc.uri.fsPath);
    execFile(bin, ['lint', doc.uri.fsPath, '--json'],
        { cwd, timeout: 15000, maxBuffer: 4 * 1024 * 1024 },
        (err, stdout, stderr) => {
            const diags = [];
            let items = [];
            try { items = JSON.parse(stdout || '[]'); } catch (e) { /* 老版无 --json 输出 */ }
            for (const it of items) {
                const sev = it.level === '错误' ? vscode.DiagnosticSeverity.Error
                          : it.level === '警告' ? vscode.DiagnosticSeverity.Warning
                          : vscode.DiagnosticSeverity.Information;
                // lint 输出无行号 → 全部挂到第 1 行（文件级诊断）
                diags.push(new vscode.Diagnostic(
                    new vscode.Range(0, 0, 0, 120),
                    (it.collection ? '[' + it.collection + '] ' : '') + (it.message || ''),
                    sev));
            }
            if (err && !stdout && err.code !== 1) {
                diags.push(new vscode.Diagnostic(
                    new vscode.Range(0, 0, 0, 120),
                    'netparse lint 执行失败: ' + (err.message || err),
                    vscode.DiagnosticSeverity.Warning));
            }
            diagColl.set(doc.uri, diags);
        });
}

// ── netparse 可执行路径：execPath 优先，兼容旧 lintPath ──
function getExecPath() {
    const cfg = vscode.workspace.getConfiguration('netparse');
    return cfg.get('execPath') || cfg.get('lintPath', 'netparse');
}

let lastSelftestJson = null;   // 上次自测试的完整 JSON（导出用）

// ── 辅助：扫描文档声明 ──
function declaredFields(doc) {
    const out = new Map();
    let mode = null;
    for (let i = 0; i < doc.lineCount; i++) {
        const t = doc.lineAt(i).text;
        const st = t.trim();
        if (!st || st.startsWith('#')) continue;
        const low = st.toLowerCase();
        if (['[value]', '[rule]', '[python]', '[python_file]'].includes(low)) { mode = low; continue; }
        if (mode === '[value]' && /^\w+\s+type\(/.test(st)) {
            const m = st.match(/^(\w+)\s+type\(/);
            if (m) out.set(m[1], 'L' + (i + 1));
        }
        if (/^[A-Z]\w*\s+type\(/.test(st)) { mode = null; }
    }
    return out;
}

function declaredCollections(doc) {
    const out = [];
    for (let i = 0; i < doc.lineCount; i++) {
        const m = doc.lineAt(i).text.match(/^([A-Z]\w*)\s+type\(/);
        if (m) out.push(m[1]);
    }
    return out;
}

function pyRefs(doc) {
    const refs = [];
    let inPyFile = false;
    for (let i = 0; i < doc.lineCount; i++) {
        const t = doc.lineAt(i).text.trim();
        if (t.toLowerCase() === '[python_file]') { inPyFile = true; continue; }
        if (/^[A-Z]\w*\s+type\(/.test(t)) { inPyFile = false; continue; }
        if (inPyFile && t) refs.push(t);
    }
    return refs;
}

function escape(s) { return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'); }
function item(label, detail, kind) {
    const it = new vscode.CompletionItem(label, kind);
    it.detail = detail;
    return it;
}

module.exports = { activate, deactivate };


// ═══ [__main__] CodeLens 与自测试执行 ═══
class CmdbCodeLensProvider {
    provideCodeLenses(doc) {
        const lenses = [];
        // [__test__.NN] 段头 CodeLens：运行该测试段
        for (let i = 0; i < doc.lineCount; i++) {
            const t = doc.lineAt(i).text.trim();
            const m = t.match(/^\[__test__(?:\.(\d+))?\]$/i);
            if (m) {
                const id = m[1] || '00';
                lenses.push(new vscode.CodeLens(
                    new vscode.Range(i, 0, i, t.length),
                    { title: '\u25B6 \u8fd0\u884c\u6d4b\u8bd5 ' + id,
                      command: 'netparse.runTestSection',
                      arguments: [doc.uri, id] }));
            }
        }
        let inMain = false;
        let mainLine = -1;
        for (let i = 0; i < doc.lineCount; i++) {
            const t = doc.lineAt(i).text.trim();
            if (t.toLowerCase() === '[__main__]') { inMain = true; mainLine = i; break; }
        }
        if (mainLine >= 0) {
            // 跳过空行向后找第一行有效代码（CRLF/空行容忍）
            let hasCode = false;
            for (let j = mainLine + 1; j < doc.lineCount; j++) {
                const t2 = doc.lineAt(j).text;
                if (t2.trim() === '') continue;
                if (/^[A-Z]\w*\s+type\(/.test(t2.trim())) break;  // 下一集合声明 = 段结束
                hasCode = true;
                break;
            }
            if (hasCode) {
                lenses.push(new vscode.CodeLens(
                    new vscode.Range(mainLine, 0, mainLine, 12),
                    { title: '\u25B6 \u8fd0\u884c\u81ea\u6d4b\u8bd5\uff08[__main__]\uff09',
                      command: 'netparse.runSelftest',
                      arguments: [doc.uri] }));
            }
        }
        return lenses;
    }
}

async function exportSelftestJson() {
    if (!lastSelftestJson) {
        vscode.window.showWarningMessage(
            'netparse: \u8fd8\u6ca1\u6709\u8fd0\u884c\u8fc7\u81ea\u6d4b\u8bd5\uff0c\u5148\u70b9 CodeLens \u8fd0\u884c\u4e00\u6b21');
        return;
    }
    const doc = vscode.window.activeTextEditor && vscode.window.activeTextEditor.document;
    const base = (doc && doc.uri.scheme === 'file')
        ? doc.uri.fsPath.replace(/\.cmdb$/i, '') + '.selftest.json'
        : path.join(process.cwd(), 'selftest.json');
    fs.writeFileSync(base, lastSelftestJson, 'utf-8');
    outChannel.show(true);
    outChannel.appendLine('[netparse] \u5b8c\u6574 JSON \u5df2\u5bfc\u51fa: ' + base);
    vscode.window.showInformationMessage('netparse \u5b8c\u6574 JSON \u5df2\u4fdd\u5b58: ' + base);
}

async function runTestSection(uri, id) {
    const bin = enginePath();
    if (!outChannel) outChannel = vscode.window.createOutputChannel('netparse');
    outChannel.show(true);
    const cwd = path.dirname(uri.fsPath);
    outChannel.appendLine('[netparse] 运行测试段 ' + id + ': ' + uri.fsPath);
    execFile(bin, ['parse', uri.fsPath, '-t', id],
        { cwd, timeout: 60000, maxBuffer: 8 * 1024 * 1024 },
        (err, stdout, stderr) => {
            if (stderr) outChannel.appendLine(stderr);
            if (err) {
                const msg = (err.message || '').split('\n')[0];
                outChannel.appendLine('');
                outChannel.appendLine('[FAIL] ' + msg);
                vscode.window.showErrorMessage('netparse 测试段 ' + id + ' 失败: ' + msg);
            } else {
                outChannel.appendLine('');
                outChannel.appendLine('[PASS] 测试段 ' + id + ' 全部通过');
                vscode.window.showInformationMessage('netparse 测试段 ' + id + ' 通过');
            }
        });
}

async function runSelftest(uriArg) {
    const uri = uriArg || (vscode.window.activeTextEditor && vscode.window.activeTextEditor.document.uri);
    if (!uri || uri.scheme !== 'file') {
        vscode.window.showWarningMessage('netparse: \u6ca1\u6709\u53ef\u8fd0\u884c\u7684 .cmdb \u6587\u4ef6');
        return;
    }
    activeSelftestDoc = uri;
    outChannel.clear();
    outChannel.show(true);
    const bin = getExecPath();
    const cwd = path.dirname(uri.fsPath);
    outChannel.appendLine('[netparse] \u8fd0\u884c\u81ea\u6d4b\u8bd5: ' + uri.fsPath);
    outChannel.appendLine('[netparse] \u5f15\u64ce: ' + bin);
    outChannel.appendLine('');
    // parse 模板会自动执行 [__main__] 段——stdout/stderr 都展示
    execFile(bin, ['parse', uri.fsPath, '--selftest', '--json'],
        { cwd, timeout: 60000, maxBuffer: 8 * 1024 * 1024 },
        (err, stdout, stderr) => {
            if (stderr) outChannel.appendLine(stderr);
            if (stdout) {
                lastSelftestJson = stdout;
                const ls = stdout.split('\n');
                outChannel.appendLine('--- \u89e3\u6790\u7ed3\u679c JSON\uff08\u524d 60 \u884c\uff09 ---');
                outChannel.appendLine(ls.slice(0, 60).join('\n'));
                if (ls.length > 60) outChannel.appendLine('... \u5171 ' + ls.length + ' \u884c\uff08\u53ef\u7528\u547d\u4ee4 "netparse: \u5bfc\u51fa\u4e0a\u6b21\u81ea\u6d4b\u8bd5\u7684\u5b8c\u6574 JSON" \u5bfc\u51fa\uff09');
            }
            if (err) {
                const msg = (err.message || '').split('\n')[0];
                outChannel.appendLine('');
                outChannel.appendLine('[FAIL] ' + msg);
                vscode.window.showErrorMessage('netparse \u81ea\u6d4b\u8bd5\u5931\u8d25: ' + msg);
            } else {
                outChannel.appendLine('');
                outChannel.appendLine('[PASS] \u81ea\u6d4b\u8bd5\u901a\u8fc7\uff08\u6a21\u677f\u52a0\u8f7d\u65e0\u5f02\u5e38\uff09');
                vscode.window.showInformationMessage('netparse \u81ea\u6d4b\u8bd5\u901a\u8fc7');
            }
        });
}
