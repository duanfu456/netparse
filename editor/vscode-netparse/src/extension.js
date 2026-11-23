// netparse CMDB 第二档：自动补全 + lint 实时诊断 + 定义跳转
const vscode = require('vscode');
const { execFile } = require('child_process');
const path = require('path');
const fs = require('fs');

// ── 诊断集合 ──
const diagColl = vscode.languages.createDiagnosticCollection('netparse');

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
    const cfg = vscode.workspace.getConfiguration('netparse');
    const bin = cfg.get('lintPath', 'netparse');
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
