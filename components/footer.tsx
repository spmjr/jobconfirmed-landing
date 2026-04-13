import { MessageSquare } from "lucide-react"

export function Footer() {
  return (
    <footer className="px-6 py-12 border-t border-border">
      <div className="max-w-6xl mx-auto flex flex-col md:flex-row items-center justify-between gap-4">
        <div className="flex items-center gap-2">
          <MessageSquare className="w-5 h-5 text-primary" />
          <span className="font-semibold text-foreground">JobConfirmed</span>
        </div>
        
        <p className="text-sm text-muted-foreground">
          © {new Date().getFullYear()} JobConfirmed. All rights reserved.
        </p>
      </div>
    </footer>
  )
}
