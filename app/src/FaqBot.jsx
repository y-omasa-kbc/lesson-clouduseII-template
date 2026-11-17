import { useState } from 'react'
import { apiFetch } from './api.js'

// 社内FAQボット（コマ16。教員が用意したもの）
//   質問を API（/api/ask）に送り、回答と出典を表示する。
//   どの文書を見てよいかは、API が利用者から決める。画面からはグループを送らない。
//   「検索の候補」は、見えてはいけない文書が混ざっていないかを演習で確かめるための表示である。

export default function FaqBot({ user }) {
  const [question, setQuestion] = useState('')
  const [result, setResult] = useState(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  async function ask(e) {
    e.preventDefault()
    if (!question.trim() || busy) return
    setBusy(true)
    setError('')
    setResult(null)
    try {
      const res = await apiFetch('/api/ask', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ question }),
      })
      const data = await res.json()
      if (!res.ok) {
        setError(data.error ?? `回答できませんでした（HTTP ${res.status}）`)
        return
      }
      setResult(data)
    } catch (err) {
      setError(`回答できませんでした（${err.message}）`)
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="assistant">
      <div className="assistant-head">
        <h2>社内FAQボット</h2>
      </div>
      <form className="chat-form" onSubmit={ask}>
        <input
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          maxLength={1000}
          placeholder="質問（架空の内容だけ）"
          disabled={busy || !user}
        />
        <button type="submit" disabled={busy || !user}>
          {busy ? '回答中…' : '質問'}
        </button>
      </form>
      {error && <p className="message">{error}</p>}
      {result && (
        <div className="tool-step">
          <div>{result.answer}</div>
          {result.citations.length > 0 && (
            <ul className="hint">
              {result.citations.map((c) => (
                <li key={c.n}>
                  出典 [{c.n}] {c.source}「{c.title}」
                </li>
              ))}
            </ul>
          )}
          <div className="item-id">
            グループ: {result.groups.join(', ') || '（なし）'} ／ 検索の候補: {result.searched.join(', ') || '（なし）'}{' '}
            ／ LLM を呼んだ回数: {result.llmCalls}
          </div>
        </div>
      )}
    </section>
  )
}
