#!/usr/bin/env node
'use strict'

// Protocol client for the compatibility runner: supplies player presence, chat capture, and
// player-issued commands. Speaks JSON lines: events on stdout, requests on stdin. Its exit code
// is never evidence; the runner judges only the events it reports.

const mineflayer = require('mineflayer')
const readline = require('node:readline')

function parseArgs (argv) {
  const options = {}
  for (let i = 0; i < argv.length; i += 2) {
    if (!argv[i].startsWith('--') || argv[i + 1] === undefined) {
      throw new Error(`bad argument ${argv[i]}`)
    }
    options[argv[i].slice(2)] = argv[i + 1]
  }
  for (const required of ['host', 'port', 'username', 'version']) {
    if (!options[required]) throw new Error(`missing --${required}`)
  }
  return options
}

function emit (event, data = {}) {
  process.stdout.write(JSON.stringify({ t: new Date().toISOString(), event, ...data }) + '\n')
}

function describe (value) {
  if (value === undefined || value === null) return String(value)
  if (typeof value === 'string') return value
  if (value instanceof Error) return value.stack || String(value)
  try { return JSON.stringify(value) } catch { return String(value) }
}

let options
try {
  options = parseArgs(process.argv.slice(2))
} catch (error) {
  emit('error', { message: describe(error), fatal: true })
  process.exit(2)
}

emit('starting', { mineflayer: require('mineflayer/package.json').version, node: process.version })

const bot = mineflayer.createBot({
  host: options.host,
  port: Number(options.port),
  username: options.username,
  version: options.version,
  auth: 'offline',
  hideErrors: true,
  logErrors: false
})

const position = () => {
  const p = bot.entity && bot.entity.position
  return p ? { x: p.x, y: p.y, z: p.z } : null
}

bot.once('login', () => emit('login', { version: bot.version, protocol: bot.protocolVersion }))
bot.on('spawn', () => {
  bot.physicsEnabled = false
  emit('spawn', { position: position(), gameMode: bot.game && bot.game.gameMode })
})
bot.on('messagestr', (message, messagePosition) => emit('chat', { text: message, position: messagePosition }))
bot.on('kicked', (reason, loggedIn) => emit('kicked', { reason: describe(reason), loggedIn }))
bot.on('error', error => emit('error', { message: describe(error) }))
bot.on('end', reason => {
  emit('end', { reason: describe(reason) })
  setTimeout(() => process.exit(0), 100)
})

readline.createInterface({ input: process.stdin })
  .on('line', line => {
    let request
    try {
      request = JSON.parse(line)
    } catch {
      emit('error', { message: `unparseable request ${line}` })
      return
    }
    try {
      if (request.cmd === 'chat') {
        bot.chat(request.text)
        emit('sent', { id: request.id, text: request.text })
      } else if (request.cmd === 'position') {
        emit('position', { id: request.id, position: position() })
      } else if (request.cmd === 'quit') {
        bot.quit('compatibility runner finished')
      } else {
        emit('error', { message: `unknown request ${request.cmd}` })
      }
    } catch (error) {
      emit('error', { id: request.id, message: describe(error) })
    }
  })
  .on('close', () => {
    try { bot.quit('runner closed stdin') } catch {}
    setTimeout(() => process.exit(0), 500)
  })
