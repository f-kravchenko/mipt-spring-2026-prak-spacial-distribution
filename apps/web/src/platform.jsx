import React from 'react';
import './platform.css';

// Follow the explicit map preference on every page; otherwise CSS follows the OS.
try {
  const theme = localStorage.getItem('rutwin-theme');
  if (theme === 'light' || theme === 'dark') document.documentElement.dataset.theme = theme;
} catch { /* Storage can be unavailable in private browsing. */ }

export function EnvironmentBadge() {
  const label = {
    'source.rutwin.ru': 'Разработка',
    'rutwin.ru': 'Тест',
    'demo2.rutwin.ru': 'Сценарий',
  }[window.location.hostname];
  return label ? <span className="rw-environment">{label}</span> : null;
}

export function Brand() {
  return <a className="rw-brand" href="/" aria-label="Рутвин — Лаборатория территорий. На карту">
    <span className="rw-mark" aria-hidden="true">Р</span>
    <span className="rw-brand-copy"><span>Рутвин<EnvironmentBadge /></span><small>Лаборатория территорий</small></span>
  </a>;
}

export function PlatformNavigation({ current }) {
  return <nav className="rw-navigation" aria-label="Разделы платформы">
    {[['map', '/', 'Карта'], ['table', 'https://demo.rutwin.ru/table', 'Показатели'], ['rating', 'https://demo.rutwin.ru/rating.html', 'Рейтинг']].map(([id, href, label]) =>
      <a key={id} href={href} aria-current={current === id ? 'page' : undefined}>{label}</a>)}
  </nav>;
}
