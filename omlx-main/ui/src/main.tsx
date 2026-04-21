import React from 'react';
import ReactDOM from 'react-dom/client';
import { QueryClientProvider } from '@tanstack/react-query';
import { BrowserRouter } from 'react-router-dom';
import App from './App';
import { queryClient } from './lib/queryClient';
import './index.css';

// `import.meta.env.BASE_URL` is `/` in dev (vite default) and `/ui/` in the
// production build (see `vite.config.ts`). Strip the trailing slash for
// react-router's `basename` prop.
const routerBase = import.meta.env.BASE_URL.replace(/\/$/, '');

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter basename={routerBase}>
        <App />
      </BrowserRouter>
    </QueryClientProvider>
  </React.StrictMode>,
);
