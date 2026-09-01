/**
 * SPA entry point. Owner: person 1.
 * Stub: mounts App and nothing else. Routing, layout and theming are person 1's.
 */
import React from 'react';
import ReactDOM from 'react-dom/client';
import App from './App';
import './index.css';

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
