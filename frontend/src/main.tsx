import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { createBrowserRouter, RouterProvider } from 'react-router'
import { ApiError } from './api/client'
import { AppShell } from './components/AppShell'
import './index.css'
import { LoginPage, SignupPage } from './pages/AuthPages'
import { FilePage } from './pages/FilePage'
import { FilesPage } from './pages/FilesPage'
import { HomePage } from './pages/HomePage'
import { EditFilePage, NewFilePage } from './pages/NewFilePage'
import { NotFoundPage } from './pages/NotFoundPage'
import { SignPage } from './pages/SignPage'
import { TemplatePage } from './pages/TemplatePage'
import { SettingsPage } from './pages/SettingsPage'
import { TemplatesPage } from './pages/TemplatesPage'
import { UploadPage } from './pages/UploadPage'

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 10_000,
      // Don't hammer the API on 4xx (auth, not found); retry transient failures.
      retry: (count, error) => !(error instanceof ApiError && error.status < 500) && count < 2,
    },
  },
})

const router = createBrowserRouter([
  { path: '/', element: <HomePage /> },
  { path: '/login', element: <LoginPage /> },
  { path: '/signup', element: <SignupPage /> },
  { path: '/sign/:token', element: <SignPage /> },
  {
    element: <AppShell />,
    children: [
      { path: '/files', element: <FilesPage /> },
      { path: '/files/new', element: <NewFilePage /> },
      { path: '/files/:id', element: <FilePage /> },
      { path: '/files/:id/edit', element: <EditFilePage /> },
      { path: '/templates', element: <TemplatesPage /> },
      { path: '/templates/new', element: <UploadPage /> },
      { path: '/templates/:id', element: <TemplatePage /> },
      { path: '/settings', element: <SettingsPage /> },
    ],
  },
  { path: '*', element: <NotFoundPage /> },
])

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  </StrictMode>,
)
