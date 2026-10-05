import type { Register } from 'claude-code'

type Word = { id: string; es: string; en: string }
type Piece = { text: string; bold?: boolean; code?: boolean; word?: Word }
type Line = { indent: number; chunks: Piece[][] }
type Index = { exact: Map<string, Word>; stems: { stem: string; endings: string[]; word: Word }[] }

// Relative to $HOME; the ambient-spanish skill rewrites it when a batch is promoted.
const VOCAB_FILE = '.codex/state/ambient-spanish/vocabulary.txt'
const MAX_SEEN = 80
const PLACEHOLDER = 'ES  hover a highlighted word for its English'
// Flip if the terminal shows no bullet, or two, on a redrawn reply's first block.
const DRAW_BULLET = false

// Blocks the redraw cannot reproduce; the engine's own markdown drawing stays for them.
const KEEPS_ENGINE_DRAWING = /```|^\s*\||^#{1,6}\s|^>\s|\]\(|^\s*(---|___)\s*$/m

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
  const bulleted = raw.replace(/^(\s*)[-*]\s+/, '$1• ')
  const indent = bulleted.length - bulleted.trimStart().length
  const chunks: Piece[][] = []
  let chunk: Piece[] = []
  const close = () => {
    if (chunk.length) chunks.push(chunk)
    chunk = []
  }

  for (const seg of bulleted.trim().split(/(`[^`]*`|\*\*[^*]+\*\*)/)) {
    if (!seg) continue
    const isCode = seg.startsWith('`') && seg.endsWith('`') && seg.length > 1
    const isBold = seg.startsWith('**') && seg.endsWith('**') && seg.length > 4
    const body = isCode ? seg.slice(1, -1) : isBold ? seg.slice(2, -2) : seg

    for (const part of body.split(/(\s+)/)) {
      if (!part) continue
      if (/^\s+$/.test(part)) close()
      else if (isCode) chunk.push({ text: part, code: true })
      else chunk.push(...pieceRun(index, part, { bold: isBold }))
    }
  }
  close()
  return { indent, chunks }
}

let indexLoad: Promise<Index | null> | undefined
let hasWarned = false
let isDirty = false
// Insertion order is recency; the band draws one hidden reveal per entry.
const seen = new Map<string, Word>()

function loadIndex($: any): Promise<Index | null> {
  indexLoad ??= (async () => {
    try {
      const home = await $.env.get('HOME')
      return buildIndex(await $.fs.read(`${home}/${VOCAB_FILE}`))
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

function remember(word: Word) {
  if (!seen.has(word.id)) isDirty = true
  seen.delete(word.id)
  seen.set(word.id, word)
  if (seen.size > MAX_SEEN) seen.delete(seen.keys().next().value as string)
}

export const register: Register = on => {
  on('ui.render', { component: 'AssistantMessage' }, async ($, e, next) => {
    if (KEEPS_ENGINE_DRAWING.test(e.props.text)) return next(e)
    const index = await loadIndex($)
    if (!index) return next(e)

    const lines = e.props.text.split('\n').map(line => parseLine(index, line))
    const hits = lines.flatMap(l => l.chunks.flat()).filter(p => p.word)
    if (!hits.length) return next(e)
    for (const p of hits) remember(p.word as Word)
    // The reply finishes drawing after turn.complete, so the band must be told here.
    if (isDirty) {
      isDirty = false
      $.ui.invalidate('ui.render')
    }

    const { Box, Text } = $.ui.resolve(e)
    const style = (p: Piece) => ({ bold: p.bold, color: p.code ? 'yellow' : undefined })

    const drawPiece = (p: Piece) =>
      p.word ? (
        <Text underline hover={{ scope: `es-${p.word.id}`, bold: true, color: 'cyan' }}>
          {p.text}
        </Text>
      ) : (
        <Text {...style(p)}>{p.text}</Text>
      )

    return (
      <Box flexDirection="row">
        {DRAW_BULLET && e.props.isFirstOfReply ? <Text>{'● '}</Text> : null}
        <Box flexDirection="column" flexGrow={1}>
          {lines.map(line =>
            line.chunks.length ? (
              <Box flexDirection="row" flexWrap="wrap" columnGap={1} paddingLeft={line.indent}>
                {line.chunks.map(chunk =>
                  chunk.length === 1 ? (
                    drawPiece(chunk[0])
                  ) : (
                    <Box flexDirection="row">{chunk.map(drawPiece)}</Box>
                  ),
                )}
              </Box>
            ) : (
              <Text> </Text>
            ),
          )}
        </Box>
      </Box>
    )
  })

  on('ui.render', { component: 'AbovePrompt' }, ($, e, next) => {
    if (e.props.hasSurvey || !seen.size) return next(e)

    const { Box, Text } = $.ui.resolve(e)
    const pad = PLACEHOLDER.length + 2

    // One row always, so a reveal never changes the layout under the pointer.
    return (
      <Box height={1}>
        <Text dimColor>{PLACEHOLDER}</Text>
        {[...seen.values()].map(w => (
          <Box position="absolute" top={0} left={0} display="none" hover={{ scope: `es-${w.id}`, display: 'flex' }}>
            <Text color="cyan">{`${w.es} = ${w.en}`.padEnd(pad)}</Text>
          </Box>
        ))}
      </Box>
    )
  })

  // The band draws before a reply's words are known; redraw it once the turn is over.
  on('turn.complete', ($, e, next) => {
    if (isDirty) {
      isDirty = false
      $.ui.invalidate('ui.render')
    }
    return next(e)
  })
}
