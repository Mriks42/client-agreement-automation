import { useRef, useState, type DragEvent, type FormEvent } from 'react'
import { Link, useNavigate } from 'react-router'
import { useUploadTemplate } from '../api/hooks'
import { Alert, Button, TextInput } from '../components/ui'

const MAX_MB = 20

export function UploadPage() {
  const upload = useUploadTemplate()
  const navigate = useNavigate()
  const inputRef = useRef<HTMLInputElement>(null)
  const [file, setFile] = useState<File | null>(null)
  const [name, setName] = useState('')
  const [dragging, setDragging] = useState(false)
  const [localError, setLocalError] = useState<string | null>(null)

  const choose = (f: File | undefined) => {
    setLocalError(null)
    upload.reset()
    if (!f) return
    if (f.size > MAX_MB * 1024 * 1024) {
      setLocalError(`That file is larger than ${MAX_MB} MB. Upload a smaller one.`)
      return
    }
    setFile(f)
  }

  const onDrop = (e: DragEvent) => {
    e.preventDefault()
    setDragging(false)
    choose(e.dataTransfer.files[0])
  }

  const submit = (e: FormEvent) => {
    e.preventDefault()
    if (!file) return
    upload.mutate(
      { file, name: name.trim() || undefined },
      { onSuccess: (t) => navigate(`/templates/${t.id}`) },
    )
  }

  const isWord = file?.name.toLowerCase().endsWith('.docx')

  return (
    <main className="mx-auto max-w-2xl px-4 py-8">
      <Link to="/templates" className="text-sm text-slate-500 hover:text-slate-800">
        ← Agreements
      </Link>
      <h1 className="mt-2 text-2xl font-semibold tracking-tight">Upload your client agreement</h1>
      <p className="mt-1 text-sm text-slate-600">
        Use the version you send to every client, with its blanks still empty. We’ll find each blank
        and signature line, and you check them before anything is sent.
      </p>

      <form onSubmit={submit} className="mt-6 space-y-5">
        <div
          onDragOver={(e) => {
            e.preventDefault()
            setDragging(true)
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={onDrop}
          className={`flex flex-col items-center justify-center rounded-xl border-2 border-dashed bg-white px-6 py-10 text-center transition ${dragging ? 'border-brand bg-brand/5' : 'border-slate-300'}`}
        >
          {file ? (
            <>
              <p className="font-medium">{file.name}</p>
              <p className="mt-1 text-sm text-slate-500">{(file.size / 1024 / 1024).toFixed(1)} MB</p>
              <button type="button" className="mt-3 text-sm font-medium text-brand" onClick={() => inputRef.current?.click()}>
                Choose a different file
              </button>
            </>
          ) : (
            <>
              <p className="font-medium">Drop the agreement here</p>
              <p className="mt-1 text-sm text-slate-500">PDF or Word (.docx), up to {MAX_MB} MB</p>
              <Button variant="secondary" className="mt-4" onClick={() => inputRef.current?.click()}>
                Choose a file
              </Button>
            </>
          )}
          <input
            ref={inputRef}
            type="file"
            accept=".pdf,.docx,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            className="sr-only"
            onChange={(e) => choose(e.target.files?.[0])}
          />
        </div>

        <TextInput
          label="Name (optional)"
          hint="How it’s listed here. Defaults to the file name."
          maxLength={120}
          value={name}
          onChange={(e) => setName(e.target.value)}
        />

        {(localError || upload.isError) && (
          <Alert title="We couldn’t use that file">{localError ?? upload.error?.message}</Alert>
        )}

        <div className="flex items-center gap-3">
          <Button type="submit" disabled={!file} busy={upload.isPending}>
            {upload.isPending ? (isWord ? 'Converting the Word file…' : 'Uploading…') : 'Upload and analyze'}
          </Button>
          {upload.isPending && isWord && (
            <span className="text-sm text-slate-500">Word files can take up to a minute to convert.</span>
          )}
        </div>
      </form>
    </main>
  )
}
