import type { Register } from 'claude-code'

type Word = { id: string; es: string; en: string }
type Piece = { text: string; bold?: boolean; italic?: boolean; strike?: boolean; code?: boolean; dim?: boolean; word?: Word }
type Line = { indent: number; chunks: Piece[][]; isRule?: boolean; codeText?: string }
// Blocks without a vocabulary word, and fenced code, go to the engine's own drawing so they
// look exactly like an ordinary reply. Only paragraphs holding a word are redrawn by the mod.
type Block =
  | { kind: 'lines'; lines: Line[] }
  | { kind: 'engine'; raw: string }
  | { kind: 'markdown'; raw: string }
  | { kind: 'code'; source: string; language?: string }
  | { kind: 'spacer' }
type Index = { exact: Map<string, Word>; english: Set<string> }

// Relative to $HOME, unless AMBIENT_SPANISH_STATE moves the state; rewritten when weekly words land.
const VOCAB_FILE = '.codex/state/ambient-spanish/vocabulary.txt'
const MAX_SEEN = 80
const MAX_PINNED = 8
const PLACEHOLDER = 'ES  hover a highlighted word for its English'
// Flip if the terminal shows no bullet, or two, on a redrawn reply's first block.
const DRAW_BULLET = false

// Table rows cannot be reproduced; they go through the engine's own drawing, block by block.
const TABLE_ROW = /^\s*\|/
const FENCE_LINE = /^\s*```/
const FENCE_LANGUAGE = /^\s*```\s*([\w+#.-]+)/
// The engine's Code and Markdown elements take at most this many characters.
const MAX_NATIVE = 10000
const HEADING = /^#{1,6}\s+/
const QUOTE = /^>\s?/
const ITEM_START = /^\s*([-*+]|\d+[.)])\s|^#{1,6}\s|^>/
const RULE_LINE =/^\s*(---|___)\s*$/
const RULE_WIDTH = 40
const INLINE_SEGMENT = /(`[^`]*`|\*\*[^*]+\*\*|\*[^*\s][^*]*\*|~~[^~]+~~|\[[^\]]*\]\([^)]*\))/
const LINK = /^\[([^\]]*)\]\(([^)]*)\)$/

// Vocabulary forms that are also ordinary English words.
const ENGLISH_HOMOGRAPHS = new Set(['color', 'final', 'red', 'pan', 'pie', 'sol', 'solo', 'mes', 'mal', 'ser', 'dar', 'ver', 'ir', 'mano', 'media', 'dos', 'cara', 'mesa', 'plaza', 'tapa', 'copia'])

// Common English function words; a token in this set is never underlined.
const ENGLISH_FUNCTION_WORDS = `the a an this that these those my your his her its our their me you him us them i he she we they it
is are was were be been being am do does did done have has had having will would can could shall should may might must
and or but nor so yet if than then as because while although though
in on at by for with from to of into onto over under about after before between through during up down out off
not no yes all any some each every both either neither other another such only own same too very just also
what which who whom whose when where why how there here now more most much many few less than`.split(/\s+/)

// English words that a generated or exact Spanish form spells, found by checking the
// full A0–C1 lexicon against an English dictionary, and no gloss already covers.
const ENGLISH_LOOKALIKES = `accede actual aisle amen anterior arena ate cargo carbon carton cartel cede circular
coda coma combustible concede conductor conserve constructor construe corporal criteria critique culpable
curriculum curse diversion dole dote dude echo eleven fallen fatal favorable filial fin gene gratis impede
impute incline integral inversion labor lance lee lesion librarian maxima median minima mania manifesto mire
mole motive motor natal pare patron posterior postal potable principal probe procure progenitor regimen regular
remover repose reunion revolver rogue rumor salve salvo sensible sole sonar surge talon tan temporal timbre toss
tress tribunal valor via vocal`.split(/\s+/)

const STRIP_ACCENTS = /[̀-ͯ]/g
// Keeps n-tilde as its own letter ("una" is not "una with a tilde"), like _accentless in ambient_state.py.
const normalize = (s: string) =>
  s.toLowerCase().normalize('NFC').split('ñ').map(part => part.normalize('NFD').replace(STRIP_ACCENTS, '')).join('ñ')

function addEnglish(set: Set<string>, w: string) {
  set.add(w)
  // Inflecting "to" or a possessive's "s" would hide Spanish tos and sed.
  if (w.length < 3) return
  set.add(w + 's')
  set.add(w + 'es')
  set.add(w + 'ed')
  set.add(w + 'd')
  set.add(w + 'ing')
  if (w.endsWith('e')) set.add(w.slice(0, -1) + 'ing')
  if (w.endsWith('y')) {
    set.add(w.slice(0, -1) + 'ies')
    set.add(w.slice(0, -1) + 'ied')
  }
}

const AR = { pres: ['o', 'as', 'a', 'amos', 'ais', 'an'], pret: ['e', 'aste', 'o', 'amos', 'asteis', 'aron'], imp: ['aba', 'abas', 'aba', 'abamos', 'abais', 'aban'], subj: ['e', 'es', 'e', 'emos', 'eis', 'en'], ger: 'ando', part: 'ad' }
const ER = { pres: ['o', 'es', 'e', 'emos', 'eis', 'en'], pret: ['i', 'iste', 'io', 'imos', 'isteis', 'ieron'], imp: ['ia', 'ias', 'ia', 'iamos', 'iais', 'ian'], subj: ['a', 'as', 'a', 'amos', 'ais', 'an'], ger: 'iendo', part: 'id' }
const IR = { ...ER, pres: ['o', 'es', 'e', 'imos', 'is', 'en'] }
const FUTURE = ['e', 'as', 'a', 'emos', 'eis', 'an']
const CONDITIONAL = ['ia', 'ias', 'ia', 'iamos', 'iais', 'ian']

// Regular forms of a verb, accents already stripped (the same way tokens are normalized).
function verbForms(inf: string): string[] {
  const kind = inf.slice(-2)
  const stem = inf.slice(0, -2)
  if (stem.length < 2 || !['ar', 'er', 'ir'].includes(kind)) return []
  const set = kind === 'ar' ? AR : kind === 'er' ? ER : IR
  // Spelling that keeps the sound before e (-ar) or before a/o (-ger, -gir).
  let soft = stem
  if (kind === 'ar') {
    if (stem.endsWith('c')) soft = stem.slice(0, -1) + 'qu'
    else if (stem.endsWith('g')) soft = stem + 'u'
    else if (stem.endsWith('z')) soft = stem.slice(0, -1) + 'c'
  }
  const hard = kind !== 'ar' && stem.endsWith('g') ? stem.slice(0, -1) + 'j' : stem
  const out: string[] = []
  const add = (base: string, endings: string[]) => { for (const e of endings) out.push(base + e) }
  const front = kind === 'ar' ? soft : stem // before e in -ar, otherwise the plain stem
  add(hard, [set.pres[0]])
  add(stem, set.pres.slice(1))
  out.push(front + set.pret[0])
  add(stem, set.pret.slice(1))
  add(stem, set.imp)
  add(inf, FUTURE)
  add(inf, CONDITIONAL)
  add(kind === 'ar' ? soft : hard, set.subj)
  out.push(stem + set.ger)
  for (const e of ['o', 'a', 'os', 'as']) out.push(stem + set.part + e)
  return out
}

function buildIndex(raw: string): Index {
  const exact = new Map<string, Word>()
  const english = new Set<string>([...ENGLISH_FUNCTION_WORDS, ...ENGLISH_LOOKALIKES, ...ENGLISH_HOMOGRAPHS])
  const derived: [string, Word][] = []
  let pos = ''

  for (const line of raw.split('\n')) {
    if (line.startsWith('## ')) {
      pos = line.slice(3).split(' ')[0]
      continue
    }
    if (line.startsWith('#') || !line.trim()) continue

    const [id, es, en] = line.split(' | ').map(s => s.trim())
    if (!id || !es || !en || es.includes(' ')) continue

    for (const w of en.toLowerCase().split(/[^a-z]+/)) if (w) addEnglish(english, w)

    const n = normalize(es)
    if (ENGLISH_HOMOGRAPHS.has(n)) continue
    const word: Word = { id, es, en }
    exact.set(n, word)

    if (pos === 'verb') {
      const reflexive = /(ar|er|ir)se$/.test(n)
      const inf = reflexive ? n.slice(0, -2) : n
      if (reflexive) derived.push([inf, word])
      for (const form of verbForms(inf)) derived.push([form, word])
      continue
    }
    if (pos !== 'noun' && pos !== 'adjective') continue
    const forms = [n + 's', n + 'es']
    if (n.endsWith('o')) {
      const base = n.slice(0, -1)
      forms.push(base + 'a', base + 'as', base + 'os')
    }
    for (const form of forms) derived.push([form, word])
  }
  // An exact vocabulary form always beats a generated one.
  for (const [form, word] of derived) if (!exact.has(form)) exact.set(form, word)
  return { exact, english }
}

function lookup(index: Index, token: string): Word | undefined {
  const n = normalize(token)
  return index.english.has(n) ? undefined : index.exact.get(n)
}

// Splits a plain run around the Spanish words it holds.
function pieceRun(index: Index, text: string, style: { bold?: boolean; italic?: boolean; strike?: boolean }): Piece[] {
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
    const isStrike = !link && seg.startsWith('~~') && seg.endsWith('~~') && seg.length > 4
    const isItalic = !link && !isBold && !isCode && seg.startsWith('*') && seg.endsWith('*') && seg.length > 2
    const body = link
      ? link[1]
      : isCode
        ? seg.slice(1, -1)
        : isBold || isStrike
          ? seg.slice(2, -2)
          : isItalic
            ? seg.slice(1, -1)
            : seg

    for (const part of body.split(/(\s+)/)) {
      if (!part) continue
      if (/^\s+$/.test(part)) close()
      else if (isCode) chunk.push({ text: part, code: true })
      else chunk.push(...pieceRun(index, part, { bold: isBold || isHeading, italic: isItalic, strike: isStrike }))
    }
    if (link) chunk.push({ text: `(${link[2]})`, dim: true })
  }
  close()
  return { indent, chunks }
}

// Fenced code and paragraphs without a vocabulary word are drawn by the engine; tables too.
// Code lines stay unmatched so `buscar` in a command is not underlined.
function splitBlocks(index: Index, text: string): Block[] {
  const blocks: Block[] = []
  let paragraph: string[] = []
  let fence: { language?: string; rows: string[] } | null = null

  // A list item, heading or quote starts a new item; other rows continue the one before.
  // Only items holding a word are redrawn, so the rest of a list keeps the engine's own look.
  const flushParagraph = () => {
    if (!paragraph.length) return
    const items: string[][] = []
    for (const row of paragraph) {
      if (!items.length || ITEM_START.test(row)) items.push([row])
      else items[items.length - 1].push(row)
    }
    paragraph = []

    let native: string[] = []
    const flushNative = () => {
      if (!native.length) return
      const raw = native.join('\n')
      blocks.push(
        raw.length > MAX_NATIVE
          ? { kind: 'lines', lines: native.map(row => parseLine(index, row)) }
          : { kind: 'markdown', raw },
      )
      native = []
    }
    for (const rows of items) {
      const lines = rows.map(row => parseLine(index, row))
      if (lines.some(l => wordsOf(l.chunks).length) || rows.join('\n').length > MAX_NATIVE) {
        flushNative()
        blocks.push({ kind: 'lines', lines })
      } else {
        native.push(...rows)
      }
    }
    flushNative()
  }
  const flushFence = () => {
    if (!fence) return
    blocks.push({ kind: 'code', source: fence.rows.join('\n'), language: fence.language })
    fence = null
  }

  for (const row of text.split('\n')) {
    if (FENCE_LINE.test(row)) {
      if (fence) {
        flushFence()
      } else {
        flushParagraph()
        fence = { language: FENCE_LANGUAGE.exec(row)?.[1], rows: [] }
      }
    } else if (fence) {
      fence.rows.push(row)
    } else if (TABLE_ROW.test(row)) {
      flushParagraph()
      const last = blocks[blocks.length - 1]
      if (last?.kind === 'engine') last.raw += `\n${row}`
      else blocks.push({ kind: 'engine', raw: row })
    } else if (!row.trim()) {
      flushParagraph()
      blocks.push({ kind: 'spacer' })
    } else {
      paragraph.push(row)
    }
  }
  flushParagraph()
  flushFence()
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

async function vocabPath($: any): Promise<string> {
  const home = await $.env.get('HOME')
  const state: string | undefined = await $.env.get('AMBIENT_SPANISH_STATE')
  return state ? `${state.replace(/[^/]*$/, '')}vocabulary.txt` : `${home}/${VOCAB_FILE}`
}

// Hover only works in the terminal CLI; the desktop app and IDE extensions set another entrypoint.
let isCli: Promise<boolean> | undefined
const onCli = ($: any): Promise<boolean> =>
  (isCli ??= Promise.resolve($.env.get('CLAUDE_CODE_ENTRYPOINT')).then(v => v === 'cli', () => false))

function loadIndex($: any): Promise<Index | null> {
  const stale = indexLoad && Date.now() - indexAt > INDEX_REFRESH_MS
  if (stale) {
    const previous = indexLoad
    indexAt = Date.now()
    indexLoad = (async () => {
      try {
        const raw: string = await $.fs.read(await vocabPath($))
        if (raw === indexRaw) return await previous
        indexRaw = raw
        return buildIndex(raw)
      } catch {
        return await previous
      }
    })()
  }
  // A failed first load is retried at most every refresh interval, not on every render.
  if (!indexLoad && Date.now() - indexAt < INDEX_REFRESH_MS) return Promise.resolve(null)
  indexLoad ??= (async () => {
    indexAt = Date.now()
    try {
      indexRaw = await $.fs.read(await vocabPath($))
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
    if (!(await onCli($))) return next(e)
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

    const { Box, Text, Markdown, Code } = $.ui.resolve(e)
    const style = (p: Piece) => ({
      bold: p.bold,
      italic: p.italic,
      strikethrough: p.strike,
      dimColor: p.dim,
      color: p.code ? 'yellow' : undefined,
    })

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
    const drawBlock = (block: Block, i: number) => {
      switch (block.kind) {
        case 'lines':
          return block.lines.map(drawLine)
        case 'markdown':
          return <Markdown text={block.raw} />
        case 'code':
          return block.source.length > MAX_NATIVE
            ? block.source.split('\n').map(row => <Text>{row || ' '}</Text>)
            : <Code source={block.source} language={block.language} />
        case 'spacer':
          return <Text> </Text>
        default:
          return engineNodes[i] ?? block.raw.split('\n').map(row => <Text>{row}</Text>)
      }
    }

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
