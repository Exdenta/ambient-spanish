import type { Register } from 'claude-code'

type Word = { id: string; es: string; en: string }
type Piece = { text: string; bold?: boolean; code?: boolean; dim?: boolean; word?: Word }
type Line = { indent: number; chunks: Piece[][]; isRule?: boolean; codeText?: string }
type Block = { kind: 'lines'; lines: Line[] } | { kind: 'engine'; raw: string }
type Index = { exact: Map<string, Word>; stems: { stem: string; endings: string[]; word: Word }[] }

// Relative to $HOME; the ambient-spanish skill rewrites it when a batch is promoted.
const VOCAB_FILE = '.codex/state/ambient-spanish/vocabulary.txt'
const MAX_SEEN = 80
const MAX_PINNED = 8
const PLACEHOLDER = 'ES  hover a highlighted word for its English'
// Flip if the terminal shows no bullet, or two, on a redrawn reply's first block.
const DRAW_BULLET = false

// Table rows cannot be reproduced; they go through the engine's own drawing, block by block.
const TABLE_ROW = /^\s*\|/
const FENCE_LINE = /^\s*```/
const HEADING = /^#{1,6}\s+/
const QUOTE = /^>\s?/
const RULE_LINE = /^\s*(---|___)\s*$/
const RULE_WIDTH = 40
const INLINE_SEGMENT = /(`[^`]*`|\*\*[^*]+\*\*|\[[^\]]*\]\([^)]*\))/
const LINK = /^\[([^\]]*)\]\(([^)]*)\)$/

// Vocabulary forms that are also ordinary English words.
const ENGLISH_HOMOGRAPHS = new Set(['color', 'final', 'red', 'pan', 'pie', 'sol', 'solo', 'mes', 'mal', 'ser', 'dar', 'ver', 'ir', 'mano', 'media', 'dos', 'cara', 'mesa', 'plaza', 'tapa', 'copia'])

const AR_ENDINGS = ['a', 'as', 'amos', 'ais', 'an', 'o', 'e', 'es', 'en', 'ado', 'ada', 'ados', 'adas', 'ando', 'aron', 'aba', 'aban']
const ER_IR_ENDINGS = ['e', 'es', 'emos', 'en', 'o', 'a', 'as', 'an', 'amos', 'ido', 'ida', 'idos', 'idas', 'iendo', 'io', 'ia', 'ian', 'ieron', 'imos', 'is']

const normalize = (s: string) => s.toLowerCase().normalize('NFD').replace(/[̀-ͯ]/g, '')

function buildIndex(raw: string): Index {
  const exact = new Map<string, Word>()
  const stems: Index['stems'] = []
  let pos = ''

  for (const line of raw.split('\n')) {
    if (line.startsWith('## ')) {
      pos = line.slice(3).split(' ')[0]
      continue
    }
    if (line.startsWith('#') || !line.trim()) continue

    const [id, es, en] = line.split(' | ').map(s => s.trim())
    if (!id || !es || !en || es.includes(' ')) continue

    const n = normalize(es)
    if (ENGLISH_HOMOGRAPHS.has(n)) continue
    const word: Word = { id, es, en }
    exact.set(n, word)

    if (pos === 'verb') {
      if (n.length - 2 >= 3) {
        stems.push({ stem: n.slice(0, -2), endings: n.endsWith('ar') ? AR_ENDINGS : ER_IR_ENDINGS, word })
      }
      continue
    }
    for (const form of [n + 's', n + 'es']) if (!exact.has(form)) exact.set(form, word)
    if (n.endsWith('o')) {
      const base = n.slice(0, -1)
      for (const form of [base + 'a', base + 'as', base + 'os']) if (!exact.has(form)) exact.set(form, word)
    }
  }
  return { exact, stems }
}

function lookup(index: Index, token: string): Word | undefined {
  const n = normalize(token)
  const hit = index.exact.get(n)
  if (hit) return hit
  for (const { stem, endings, word } of index.stems) {
    if (n.startsWith(stem) && endings.includes(n.slice(stem.length))) return word
  }
  return undefined
}

// Splits a plain run around the Spanish words it holds.
function pieceRun(index: Index, text: string, style: { bold?: boolean }): Piece[] {
  const pieces: Piece[] = []
  let last = 0
  for (const m of text.matchAll(/[\p{L}\p{M}]+/gu)) {
    const word = lookup(index, m[0])
    if (!word) continue
    if (m.index > last) pieces.push({ text: text.slice(last, m.index), ...style })
    pieces.push({ text: m[0], word, ...style })
    last = m.index + m[0].length
  }
  if (last < text.length) pieces.push({ text: text.slice(last), ...style })
  return pieces
}

function parseLine(index: Index, raw: string): Line {
  if (RULE_LINE.test(raw)) return { indent: 0, chunks: [], isRule: true }
  const isHeading = HEADING.test(raw)
  const isQuote = !isHeading && QUOTE.test(raw)
  const stripped = isHeading ? raw.replace(HEADING, '') : isQuote ? raw.replace(QUOTE, '') : raw
  const bulleted = stripped.replace(/^(\s*)[-*]\s+/, '$1• ')
  const indent = bulleted.length - bulleted.trimStart().length
  const chunks: Piece[][] = isQuote ? [[{ text: '│', dim: true }]] : []
  let chunk: Piece[] = []
  const close = () => {
    if (chunk.length) chunks.push(chunk)
    chunk = []
  }

  for (const seg of bulleted.trim().split(INLINE_SEGMENT)) {
    if (!seg) continue
    const link = LINK.exec(seg)
    const isCode = !link && seg.startsWith('`') && seg.endsWith('`') && seg.length > 1
    const isBold = !link && seg.startsWith('**') && seg.endsWith('**') && seg.length > 4
    const body = link ? link[1] : isCode ? seg.slice(1, -1) : isBold ? seg.slice(2, -2) : seg

    for (const part of body.split(/(\s+)/)) {
      if (!part) continue
      if (/^\s+$/.test(part)) close()
      else if (isCode) chunk.push({ text: part, code: true })
      else chunk.push(...pieceRun(index, part, { bold: isBold || isHeading }))
    }
    if (link) chunk.push({ text: `(${link[2]})`, dim: true })
  }
  close()
  return { indent, chunks }
}

// Tables and nothing else go to the engine; code lines stay unmatched so `buscar` in a command is not underlined.
function splitBlocks(index: Index, text: string): Block[] {
  const blocks: Block[] = []
  let inFence = false
  const linesBlock = (): Line[] => {
    const last = blocks[blocks.length - 1]
    if (last?.kind === 'lines') return last.lines
    const lines: Line[] = []
    blocks.push({ kind: 'lines', lines })
    return lines
  }

  for (const row of text.split('\n')) {
    if (FENCE_LINE.test(row)) {
      inFence = !inFence
    } else if (inFence) {
      linesBlock().push({ indent: 0, chunks: [], codeText: row })
    } else if (TABLE_ROW.test(row)) {
      const last = blocks[blocks.length - 1]
      if (last?.kind === 'engine') last.raw += `\n${row}`
      else blocks.push({ kind: 'engine', raw: row })
    } else {
      linesBlock().push(parseLine(index, row))
    }
  }
  return blocks
}

const wordsOf = (chunks: Piece[][]): Word[] => chunks.flat().flatMap(p => (p.word ? [p.word] : []))

let indexLoad: Promise<Index | null> | undefined
let indexRaw = ''
let indexAt = 0
// New words land weekly while a session may stay open, so re-read the file now and then.
const INDEX_REFRESH_MS = 30_000
let hasWarned = false
let isDirty = false
// Insertion order is recency; the band draws one hidden reveal per entry.
const seen = new Map<string, Word>()
// Words in engine-drawn tables: nothing to hover, so the band lists them as plain text.
const pinned = new Map<string, Word>()

function loadIndex($: any): Promise<Index | null> {
  const stale = indexLoad && Date.now() - indexAt > INDEX_REFRESH_MS
  if (stale) {
    const previous = indexLoad
    indexAt = Date.now()
    indexLoad = (async () => {
      try {
        const home = await $.env.get('HOME')
        const raw: string = await $.fs.read(`${home}/${VOCAB_FILE}`)
        if (raw === indexRaw) return await previous
        indexRaw = raw
        return buildIndex(raw)
      } catch {
        return await previous
      }
    })()
  }
  indexLoad ??= (async () => {
    try {
      const home = await $.env.get('HOME')
      indexRaw = await $.fs.read(`${home}/${VOCAB_FILE}`)
      indexAt = Date.now()
      return buildIndex(indexRaw)
    } catch {
      indexLoad = undefined
      if (!hasWarned) {
        hasWarned = true
        $.ui.toast('ambient-spanish-hover: cannot read the vocabulary file')
      }
      return null
    }
  })()
  return indexLoad
}

function touch(map: Map<string, Word>, word: Word, max: number) {
  if (!map.has(word.id)) isDirty = true
  map.delete(word.id)
  map.set(word.id, word)
  if (map.size > max) map.delete(map.keys().next().value as string)
}

// The band is drawn before a reply's words are known; redraw it once they are.
function flushBand($: any) {
  if (!isDirty) return
  isDirty = false
  $.ui.invalidate('ui.render')
}

async function drawByEngine(next: any, e: any, raw: string): Promise<any> {
  try {
    return await next({ ...e, props: { ...e.props, text: raw, isFirstOfReply: false } })
  } catch {
    return null
  }
}

export const register: Register = on => {
  on('ui.render', { component: 'AssistantMessage' }, async ($, e, next) => {
    const index = await loadIndex($)
    if (!index) return next(e)

    const blocks = splitBlocks(index, e.props.text)
    const hoverWords = blocks.flatMap(b => (b.kind === 'lines' ? b.lines.flatMap(l => wordsOf(l.chunks)) : []))
    const tableWords = blocks.flatMap(b =>
      b.kind === 'engine' ? b.raw.split('\n').flatMap(row => wordsOf(parseLine(index, row).chunks)) : [],
    )
    for (const w of tableWords) touch(pinned, w, MAX_PINNED)
    for (const w of hoverWords) touch(seen, w, MAX_SEEN)
    // The reply finishes drawing after turn.complete, so the band must be told here.
    flushBand($)
    if (!hoverWords.length) return next(e)

    // Sequential: each call walks the engine's drawing chain for one table.
    const engineNodes: any[] = []
    for (const b of blocks) engineNodes.push(b.kind === 'engine' ? await drawByEngine(next, e, b.raw) : null)

    const { Box, Text } = $.ui.resolve(e)
    const style = (p: Piece) => ({ bold: p.bold, dimColor: p.dim, color: p.code ? 'yellow' : undefined })

    const drawPiece = (p: Piece) =>
      p.word ? (
        <Text underline hover={{ scope: `es-${p.word.id}`, bold: true, color: 'cyan' }}>
          {p.text}
        </Text>
      ) : (
        <Text {...style(p)}>{p.text}</Text>
      )

    const drawLine = (line: Line) =>
      line.isRule ? (
        <Text dimColor>{'─'.repeat(RULE_WIDTH)}</Text>
      ) : line.codeText !== undefined ? (
        <Text color="yellow">{line.codeText || ' '}</Text>
      ) : line.chunks.length ? (
        <Box flexDirection="row" flexWrap="wrap" columnGap={1} paddingLeft={line.indent}>
          {line.chunks.map(chunk =>
            chunk.length === 1 ? drawPiece(chunk[0]) : <Box flexDirection="row">{chunk.map(drawPiece)}</Box>,
          )}
        </Box>
      ) : (
        <Text> </Text>
      )

    // A null node means the engine call failed; the raw rows still show.
    const drawBlock = (block: Block, i: number) =>
      block.kind === 'lines'
        ? block.lines.map(drawLine)
        : (engineNodes[i] ?? block.raw.split('\n').map(row => <Text>{row}</Text>))

    return (
      <Box flexDirection="row">
        {DRAW_BULLET && e.props.isFirstOfReply ? <Text>{'● '}</Text> : null}
        <Box flexDirection="column" flexGrow={1}>
          {blocks.flatMap(drawBlock)}
        </Box>
      </Box>
    )
  })

  on('ui.render', { component: 'AbovePrompt' }, ($, e, next) => {
    if (e.props.hasSurvey || !(seen.size || pinned.size)) return next(e)

    const { Box, Text } = $.ui.resolve(e)
    const pad = PLACEHOLDER.length + 2

    // One hover row always, so a reveal never changes the layout under the pointer.
    return (
      <Box flexDirection="column">
        {seen.size ? (
          <Box height={1}>
            <Text dimColor>{PLACEHOLDER}</Text>
            {[...seen.values()].map(w => (
              <Box position="absolute" top={0} left={0} display="none" hover={{ scope: `es-${w.id}`, display: 'flex' }}>
                <Text color="cyan">{`${w.es} = ${w.en}`.padEnd(pad)}</Text>
              </Box>
            ))}
          </Box>
        ) : null}
        {pinned.size ? <Text color="cyan">{[...pinned.values()].map(w => `${w.es} = ${w.en}`).join('  ·  ')}</Text> : null}
      </Box>
    )
  })

  // The band draws before a reply's words are known; redraw it once the turn is over.
  on('turn.complete', ($, e, next) => {
    flushBand($)
    return next(e)
  })
}
