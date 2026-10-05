import { expect, test } from 'claude-code/testing'

const plugin = 'ambient-spanish-hover'
const VOCAB = '## verb (1)\nbuscar | buscar | to look for\n## noun (1)\ndato | dato | piece of data\n'

const mountMessage = ($: any, text: string) =>
  $.ui.mount({ plugin, surface: 'terminal', component: 'AssistantMessage', props: { text, isFirstOfReply: true } })

function stubEngine(on: any) {
  on('env.get', () => ({ value: '/home/test' }))
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

test('plain English is left to the engine', async ($, on) => {
  stubEngine(on)
  const message = await mountMessage($, 'Nothing to translate here, just a terminal.')
  expect(JSON.stringify(await message.drawn())).toContain('engine')
})
