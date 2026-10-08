import { useState } from "react";
import { supabase, authRequired, AUTH_REDIRECT_URL } from "@/lib/auth";
import { useAuth } from "@/lib/authContext";
import { Button } from "./ui";

const inputClass = "h-10 w-full min-w-0 rounded-md border border-border-strong bg-surface px-3 text-sm focus:border-accent focus:ring-2 focus:ring-accent/20 focus:outline-none";

export function AuthPanel() {
  const { session, ready, error: sessionError, recovering, finishRecovery } = useAuth();
  const [mode, setMode] = useState<"login" | "register" | "recover">("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!supabase || busy) return;
    setBusy(true); setMessage(null); setError(null);
    try {
      if (recovering) {
        const { error } = await supabase.auth.updateUser({ password });
        if (error) throw error;
        finishRecovery(); setPassword(""); setMessage("密码已更新，可以继续使用研究账户。"); return;
      }
      if (mode === "recover") {
        const { error } = await supabase.auth.resetPasswordForEmail(email.trim(), { redirectTo: AUTH_REDIRECT_URL });
        if (error) throw error;
        setMessage("如果该邮箱已注册，将收到重设密码邮件。请从邮件链接返回此页面设置新密码。"); return;
      }
      const result = mode === "login"
        ? await supabase.auth.signInWithPassword({ email: email.trim(), password })
        : await supabase.auth.signUp({ email: email.trim(), password, options: { emailRedirectTo: AUTH_REDIRECT_URL } });
      if (result.error) throw result.error;
      setPassword("");
      if (mode === "register" && !result.data.session) setMessage("请查收确认邮件，完成验证后再登录。若已注册，可直接使用登录。");
    } catch (err) { setError(err instanceof Error ? err.message : "登录失败，请重试。"); }
    finally { setBusy(false); }
  }

  async function signOut() {
    if (!supabase || busy) return;
    setBusy(true); setError(null);
    try { const { error } = await supabase.auth.signOut(); if (error) throw error; }
    catch (err) { setError(err instanceof Error ? err.message : "退出失败，请重试。"); }
    finally { setBusy(false); }
  }

  return <section aria-label="研究账户" className="mb-7 rounded-[10px] border border-border bg-surface-2/60 p-4">
    {recovering ? <form onSubmit={submit} className="flex flex-wrap items-end gap-3">
      <label className="min-w-0 flex-1 text-sm font-medium">设置新密码<input className={inputClass + " mt-2"} type="password" autoComplete="new-password" minLength={8} required value={password} onChange={e => setPassword(e.target.value)} disabled={busy} placeholder="至少 8 位" /></label>
      <Button type="submit" variant="primary" className="h-10" disabled={busy}>{busy ? "保存中…" : "保存新密码"}</Button>
    </form> : session ? <div className="flex flex-wrap items-center justify-between gap-3">
      <div className="min-w-0"><p className="text-xs text-fg-3">已登录 · 研究账户</p><p className="mt-1 break-all text-sm font-medium">{session.user.email}</p></div>
      <Button type="button" onClick={() => void signOut()} disabled={busy}>退出登录</Button>
    </div> : <>
      <div className="mb-3 flex flex-wrap items-baseline justify-between gap-2"><h2 className="text-sm font-semibold">{mode === "login" ? "登录研究账户" : mode === "register" ? "创建研究账户" : "重设密码"}</h2><span className="text-xs text-fg-3">{authRequired ? "登录后提交与管理自己的任务" : "账户接入预览"}</span></div>
      {!supabase ? <p role="status" className="text-xs leading-relaxed text-fg-3">登录服务尚未配置，注册与登录暂不可用。{authRequired ? "可以先浏览工作台和股票目录。" : "当前保留原有访客分析模式。"}</p> : <form onSubmit={submit} className={mode === "recover" ? "grid gap-3 sm:grid-cols-[1fr_auto]" : "grid gap-3 sm:grid-cols-[1fr_1fr_auto]"}>
        <label className="text-xs text-fg-2">邮箱<input className={inputClass + " mt-1"} autoComplete="email" type="email" required value={email} onChange={e => setEmail(e.target.value)} placeholder="name@example.com" disabled={busy || !ready} /></label>
        {mode !== "recover" && <label className="text-xs text-fg-2">密码<input className={inputClass + " mt-1"} autoComplete={mode === "login" ? "current-password" : "new-password"} type="password" required minLength={mode === "register" ? 8 : undefined} value={password} onChange={e => setPassword(e.target.value)} placeholder={mode === "register" ? "至少 8 位" : "输入密码"} disabled={busy || !ready} /></label>}
        <Button type="submit" variant="primary" className="h-10 self-end" disabled={busy || !ready}>{busy ? "处理中…" : mode === "login" ? "登录" : mode === "register" ? "注册" : "发送重设邮件"}</Button>
        <button type="button" className="min-h-9 justify-self-start text-xs font-medium text-accent hover:underline focus-visible:outline-accent" disabled={busy} onClick={() => { setMode(mode === "login" ? "register" : "login"); setError(null); setMessage(null); }}>{mode === "login" ? "还没有账户？创建账户" : "返回登录"}</button>
        {mode === "login" && <button type="button" className="min-h-9 justify-self-start text-xs text-fg-3 hover:text-accent focus-visible:outline-accent" disabled={busy} onClick={() => { setMode("recover"); setPassword(""); setError(null); setMessage(null); }}>忘记密码？</button>}
      </form>}
    </>}
    {(error || sessionError) && <p role="alert" className="mt-3 text-xs text-red-600 dark:text-red-400">{error || sessionError}</p>}
    {message && <p role="status" className="mt-3 text-xs text-accent">{message}</p>}
  </section>;
}
