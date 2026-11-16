async function request(path, options = {}) {
  const resp = await fetch(`/api${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  });
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok) {
    const detail = data.detail;
    throw new Error(typeof detail === 'string' ? detail : `HTTP ${resp.status}`);
  }
  return data;
}

export const api = {
  health: () => request('/healthz'),
  stats: () => request('/stats'),
  ingest: (texts, options = {}) =>
    request('/ingest', { method: 'POST', body: JSON.stringify({ texts, ...options }) }),
  documents: (limit = 100, offset = 0) => request(`/documents?limit=${limit}&offset=${offset}`),
  deleteDocument: (id) => request(`/documents/${id}`, { method: 'DELETE' }),
  query: (question, topK = 3) =>
    request('/query', { method: 'POST', body: JSON.stringify({ question, top_k: topK }) }),
  retrieve: (q, topK = 3) => request(`/retrieve?q=${encodeURIComponent(q)}&top_k=${topK}`),
  getConfig: () => request('/config'),
  saveConfig: (cfg) => request('/config', { method: 'PUT', body: JSON.stringify(cfg) }),
  testConfig: (payload) => request('/config/test', { method: 'POST', body: JSON.stringify(payload) }),
};

export async function ingestFile(file, options = {}) {
  const form = new FormData();
  form.append('file', file);
  form.append('chunk', options.chunk ?? true);
  form.append('chunk_size', options.chunk_size ?? 500);
  form.append('chunk_overlap', options.chunk_overlap ?? 50);
  const resp = await fetch('/api/imports', { method: 'POST', body: form });
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok) {
    throw new Error(typeof data.detail === 'string' ? data.detail : `HTTP ${resp.status}`);
  }
  return data;
}

export async function listImports() {
  const resp = await fetch('/api/imports');
  return (await resp.json()).tasks;
}

export async function cancelImport(id) {
  const resp = await fetch(`/api/imports/${id}/cancel`, { method: 'POST' });
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok) {
    throw new Error(typeof data.detail === 'string' ? data.detail : `HTTP ${resp.status}`);
  }
  return data;
}
