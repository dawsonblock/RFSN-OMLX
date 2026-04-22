import React from 'react';
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import ChatPage from '../src/pages/ChatPage';

function jsonResp(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json' },
  });
}

function wrap(children: React.ReactNode) {
  return <MemoryRouter>{children}</MemoryRouter>;
}

describe('ChatPage', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it('loads models from /v1/models and renders them in the selector', async () => {
    global.fetch = vi.fn(async (input: RequestInfo | URL) => {
      const url = typeof input === 'string' ? input : input.toString();
      if (url.endsWith('/v1/models')) {
        return jsonResp({
          data: [
            { id: 'qwen2.5-0.5b', owned_by: 'omlx' },
            { id: 'phi-3.5-mini', owned_by: 'omlx' },
          ],
        });
      }
      return jsonResp({ detail: 'unhandled' }, 500);
    }) as typeof fetch;

    render(wrap(<ChatPage />));

    const select = (await screen.findByRole('combobox')) as HTMLSelectElement;
    await waitFor(() => expect(select.options.length).toBe(2));
    expect(select.options[0].value).toBe('qwen2.5-0.5b');
  });

  it('POSTs /v1/chat/completions and renders the assistant reply (non-streaming)', async () => {
    const calls: Array<{ url: string; init?: RequestInit }> = [];
    global.fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === 'string' ? input : input.toString();
      calls.push({ url, init });
      if (url.endsWith('/v1/models')) {
        return jsonResp({ data: [{ id: 'qwen2.5-0.5b', owned_by: 'omlx' }] });
      }
      if (url.endsWith('/v1/chat/completions')) {
        return jsonResp({
          choices: [{ message: { role: 'assistant', content: 'Hi there.' } }],
        });
      }
      return jsonResp({ detail: 'unhandled' }, 500);
    }) as typeof fetch;

    render(wrap(<ChatPage />));

    await screen.findByRole('combobox');

    // Turn off streaming so the non-SSE path is used.
    const streamToggle = screen.getByLabelText(/stream/i) as HTMLInputElement;
    if (streamToggle.checked) fireEvent.click(streamToggle);

    const textarea = screen.getByPlaceholderText(/Type a message/i);
    fireEvent.change(textarea, { target: { value: 'Hello' } });

    const sendBtn = screen.getByRole('button', { name: /^send$/i });
    fireEvent.click(sendBtn);

    await waitFor(() => expect(screen.getByText('Hi there.')).toBeTruthy());

    const chat = calls.find((c) => c.url.endsWith('/v1/chat/completions'));
    expect(chat).toBeTruthy();
    expect(chat!.init?.method).toBe('POST');
    const body = JSON.parse(String(chat!.init?.body ?? '{}'));
    expect(body.model).toBe('qwen2.5-0.5b');
    expect(body.stream).toBe(false);
    expect(Array.isArray(body.messages)).toBe(true);
    expect(body.messages[body.messages.length - 1]).toEqual({
      role: 'user',
      content: 'Hello',
    });
  });
});
