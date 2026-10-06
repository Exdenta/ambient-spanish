import { expect, test } from 'claude-code/testing'

const plugin = 'ambient-spanish-hover'
const VOCAB = '## verb (1)\nbuscar | buscar | to look for\n## noun (1)\ndato | dato | piece of data\n'

const mountMessage = ($: any, text: string) =>
  $.ui.mount({ plugin, surface: 'terminal', component: 'AssistantMessage', props: { text, isFirstOfReply: true } })

function stubEngine(on: any, entrypoint = 'cli') {
  on('env.get', (_$: any, e: any) => ({ value: e.name === 'CLAUDE_CODE_ENTRYPOINT' ? entrypoint : '/home/test' }))
  on('fs.read', () => ({ value: VOCAB }))
  on('ui.render', () => ({ type: 'Text', props: {}, children: ['engine'] }))
}

test('a known Spanish word becomes a hover target and fills the band', async ($, on) => {
  stubEngine(on)
  const message = await mountMessage($, 'I will buscar the file, then **check** the datos.')
  const drawn = JSON.stringify(await message.drawn())
  expect(drawn).toContain('es-buscar')
  expect(drawn).toContain('es-dato')

  const band = await $.ui.mount({
    plugin,
    surface: 'terminal',
    component: 'AbovePrompt',
    props: { hasSurvey: false, isWorking: false, maxRows: 5, bodyColumns: 80 },
  })
  expect(JSON.stringify(await band.drawn())).toContain('buscar = to look for')
})

test('a horizontal rule does not stop the words around it from being hover targets', async ($, on) => {
  stubEngine(on)
  const message = await mountMessage($, 'I will buscar it.\n\n---\n\nThe dato is here.')
  const drawn = JSON.stringify(await message.drawn())
  expect(drawn).toContain('es-buscar')
  expect(drawn).toContain('es-dato')
  // A paragraph without a vocabulary word, a rule included, is drawn natively.
  expect(drawn).toContain('"type":"Markdown"')
})

const mountBand = ($: any) =>
  $.ui.mount({
    plugin,
    surface: 'terminal',
    component: 'AbovePrompt',
    props: { hasSurvey: false, isWorking: false, maxRows: 5, bodyColumns: 80 },
  })

test('a table goes to the engine while its words are pinned in the band', async ($, on) => {
  stubEngine(on)
  const message = await mountMessage($, 'I will buscar it.\n\n| name | dato |\n| --- | --- |\n| a | b |\n')
  const drawn = JSON.stringify(await message.drawn())
  expect(drawn).toContain('es-buscar')
  expect(drawn).toContain('engine')
  expect(drawn).not.toContain('es-dato')
  expect(JSON.stringify(await (await mountBand($)).drawn())).toContain('dato = piece of data')
})

test('code lines are drawn as-is and never matched', async ($, on) => {
  stubEngine(on)
  const message = await mountMessage($, 'Run this:\n```\nbuscar datos\n```\nThen buscar again.')
  const drawn = JSON.stringify(await message.drawn())
  expect(drawn.split('es-buscar').length - 1).toBe(1)
  expect(drawn).toContain('buscar datos')
  expect(drawn).toContain('"type":"Code"')
})

test('a fence keeps its language so the engine highlights it', async ($, on) => {
  stubEngine(on)
  const message = await mountMessage($, 'Then buscar:\n```python\nprint(1)\n```')
  const drawn = JSON.stringify(await message.drawn())
  expect(drawn).toContain('"language":"python"')
  expect(drawn).toContain('print(1)')
})

test('italics and strikethrough are kept in redrawn paragraphs', async ($, on) => {
  stubEngine(on)
  const message = await mountMessage($, 'I will buscar *quietly* and ~~never~~ stop.')
  const drawn = JSON.stringify(await message.drawn())
  expect(drawn).toContain('"italic":true')
  expect(drawn).toContain('"strikethrough":true')
})

test('a paragraph with no vocabulary word is drawn by the engine, not redrawn', async ($, on) => {
  stubEngine(on)
  const message = await mountMessage($, 'I will buscar it.\n\n- plain **English** item\n- another one')
  const drawn = JSON.stringify(await message.drawn())
  expect(drawn).toContain('"type":"Markdown"')
  expect(drawn).toContain('plain **English** item')
})

test('headings, quotes and links are drawn by the mod with their words hoverable', async ($, on) => {
  stubEngine(on)
  const message = await mountMessage($, '# Plan buscar\n> a dato here\nSee [buscar](http://x.y)')
  const drawn = JSON.stringify(await message.drawn())
  expect(drawn).not.toContain('engine')
  expect(drawn.split('es-buscar').length - 1).toBe(2)
  expect(drawn).toContain('es-dato')
  expect(drawn).toContain('(http://x.y)')
})

test('plain English is left to the engine', async ($, on) => {
  stubEngine(on)
  const message = await mountMessage($, 'Nothing to translate here, just a terminal.')
  expect(JSON.stringify(await message.drawn())).toContain('engine')
})

test('in a list only the items holding a word are redrawn', async ($, on) => {
  stubEngine(on)
  const message = await mountMessage($, '- plain **English** item\n- I will buscar it\n- another one')
  const drawn = JSON.stringify(await message.drawn())
  expect(drawn).toContain('es-buscar')
  expect(drawn).toContain('plain **English** item')
  expect(drawn).toContain('another one')
  expect(drawn.split('"type":"Markdown"').length - 1).toBe(2)
})

test('outside the CLI the engine draws everything', async ($, on) => {
  stubEngine(on, 'claude-desktop')
  const message = await mountMessage($, 'I will buscar the file.')
  expect(JSON.stringify(await message.drawn())).not.toContain('es-buscar')
})
