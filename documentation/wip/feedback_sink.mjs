// Throwaway feedback sink for the finance test-plan HTML. Serves /load + /save on :4545,
// persisting the test-plan textareas to finance_flows_v2_feedback.json next to this file.
import { createServer } from 'node:http'
import { readFile, writeFile } from 'node:fs/promises'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'

const DIR = dirname(fileURLToPath(import.meta.url))
const FILE = join(DIR, 'finance_flows_v2_feedback.json')
const CORS = {
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Methods': 'GET,POST,OPTIONS',
  'Access-Control-Allow-Headers': 'Content-Type',
}

createServer(async (req, res) => {
  if (req.method === 'OPTIONS') { res.writeHead(204, CORS); return res.end() }
  if (req.url === '/load') {
    const body = await readFile(FILE, 'utf8').catch(() => '{}')
    res.writeHead(200, { ...CORS, 'Content-Type': 'application/json' })
    return res.end(body)
  }
  if (req.url === '/save' && req.method === 'POST') {
    let data = ''
    req.on('data', (c) => (data += c))
    req.on('end', async () => {
      await writeFile(FILE, data || '{}').catch(() => {})
      res.writeHead(200, { ...CORS, 'Content-Type': 'application/json' })
      res.end('{"ok":true}')
    })
    return
  }
  res.writeHead(404, CORS)
  res.end('{}')
}).listen(4545, () => console.log('feedback sink on :4545 →', FILE))
