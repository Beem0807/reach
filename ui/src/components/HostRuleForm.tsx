import { useState } from 'react';
import type { HostRule } from '../types';

export const EMPTY_HOST_RULE: HostRule = { bin: '', args: [] };

// Parse a command-pattern string into a structured host rule: the first token is the
// binary, the rest are positional args (each a literal or "*"). Whitespace-split only -
// there is no shell, so operators are just literal tokens (and the backend rejects them).
export function parseHostRule(text: string): HostRule {
  const toks = text.trim().split(/\s+/).filter(Boolean);
  return { bin: toks[0] ?? '', args: toks.slice(1) };
}

export function hostRuleToText(r: HostRule): string {
  return [r.bin, ...r.args].filter(Boolean).join(' ');
}

// Shell metacharacters can't appear in an execve argv token, so a rule containing them
// (other than a whole-arg "*" wildcard) can never match a job - flag it. Mirrors the
// backend's normalize_host_rule rejection.
const SHELL_META = /[|;&$`()<>*?[\]{}~'"\\]/;
export function ruleHasShellMeta(r: HostRule): boolean {
  if (r.bin && SHELL_META.test(r.bin)) return true;
  return r.args.some(a => a !== '*' && SHELL_META.test(a));
}

function Chip({ label, value }: { label: string; value: string }) {
  const wild = value === '*';
  return (
    <span className="inline-flex items-baseline gap-1">
      <span className="text-[10px] uppercase tracking-wider text-gray-400">{label}</span>
      <span className={wild ? 'text-gray-400 italic' : 'font-mono text-gray-800'}>{wild ? 'any' : value}</span>
    </span>
  );
}

// A host approval rule editor. The user types a whole command *pattern* (e.g.
// `systemctl restart *`) in one field; it's parsed into a structured {bin, args[]} rule
// (shown as chips) so there's no "typed the command in the binary box" footgun. Each arg
// is a literal or "*" (matches any single value at that position); arity is fixed.
export function HostRuleForm({ value, onChange }: { value: HostRule; onChange: (r: HostRule) => void }) {
  const [text, setText] = useState(hostRuleToText(value));
  const field = 'w-full border border-gray-300 rounded-lg px-3 py-2 text-sm font-mono focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:border-transparent';

  const onText = (t: string) => { setText(t); onChange(parseHostRule(t)); };

  return (
    <div className="space-y-2">
      <input
        value={text}
        onChange={e => onText(e.target.value)}
        placeholder="systemctl restart *"
        aria-label="Command pattern"
        className={field}
      />
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 min-h-[1.25rem]">
        {value.bin ? (
          <>
            <Chip label="bin" value={value.bin} />
            {value.args.map((a, i) => <Chip key={i} label={`arg ${i + 1}`} value={a} />)}
          </>
        ) : (
          <span className="text-xs text-gray-400">Type a command; use <span className="font-mono">*</span> for any argument (e.g. <span className="font-mono">systemctl restart *</span>).</span>
        )}
      </div>
      {ruleHasShellMeta(value) && (
        <p className="text-xs text-amber-700 bg-amber-50 border border-amber-200 rounded px-2 py-1">
          Shell characters (<span className="font-mono">$ ( ) | ; &gt; *</span> inside a word) can't match a real command - a rule with them approves nothing. Use plain args, or a whole-arg <span className="font-mono">*</span> wildcard.
        </p>
      )}
    </div>
  );
}
