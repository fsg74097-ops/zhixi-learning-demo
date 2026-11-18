import { createRoot } from 'react-dom/client';
import { App } from './App.tsx';
import './theme/tokens.css';
import './theme/workspace.css';

const preferences = document.documentElement.dataset;
preferences.theme = localStorage.getItem('zhixi.theme') ?? 'hex-light';
preferences.font = localStorage.getItem('zhixi.font') ?? 'system';

createRoot(document.getElementById('root')!).render(<App />);
