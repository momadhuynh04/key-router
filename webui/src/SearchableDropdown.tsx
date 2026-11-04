import { useEffect, useRef, useState } from 'react'

export default function SearchableDropdown({ options, value, onChange, placeholder }: {
  options: string[]
  value: string
  onChange: (val: string) => void
  placeholder: string
}) {
  const [isOpen, setIsOpen] = useState(false);
  const [search, setSearch] = useState("");
  const wrapperRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    function handleClickOutside(event: MouseEvent) {
      if (wrapperRef.current && !wrapperRef.current.contains(event.target as Node)) {
        setIsOpen(false);
      }
    }
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);

  const filteredOptions = options.filter(opt => opt.toLowerCase().includes(search.toLowerCase()));

  useEffect(() => {
    if (!isOpen) setSearch(value);
  }, [isOpen, value]);

  return (
    <div ref={wrapperRef} className="relative w-full">
      <input
        type="text"
        value={isOpen ? search : value}
        onChange={e => {
          setSearch(e.target.value);
          if (!isOpen) setIsOpen(true);
          onChange(e.target.value);
        }}
        onFocus={() => setIsOpen(true)}
        placeholder={placeholder}
        className="w-full h-9 rounded-lg bg-surface-900 border border-border px-3 pr-8 text-sm text-text-primary placeholder:text-text-muted focus:outline-none focus:ring-2 focus:ring-brand/30 focus:border-brand transition"
      />
      <div className={`absolute right-3 top-1/2 -translate-y-1/2 text-text-muted pointer-events-none transition-transform duration-200 ${isOpen ? 'rotate-180' : ''}`}>
        <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor" className="w-4 h-4">
          <path strokeLinecap="round" strokeLinejoin="round" d="M19.5 8.25l-7.5 7.5-7.5-7.5" />
        </svg>
      </div>
      {isOpen && (
        <div className="absolute z-50 w-full mt-2 bg-surface-800 rounded-xl border border-border shadow-lg max-h-60 overflow-y-auto">
          {filteredOptions.length === 0 ? (
            <div className="px-3 py-3 text-text-muted text-sm italic text-center">No matches found</div>
          ) : (
            filteredOptions.map((opt) => (
              <div
                key={opt}
                onClick={() => {
                  onChange(opt);
                  setSearch(opt);
                  setIsOpen(false);
                }}
                className={`px-3 py-2 cursor-pointer text-sm transition-colors hover:bg-white/[0.06] ${value === opt ? 'bg-brand/15 text-brand' : 'text-text-secondary'}`}
              >
                {opt}
              </div>
            ))
          )}
        </div>
      )}
    </div>
  );
}
