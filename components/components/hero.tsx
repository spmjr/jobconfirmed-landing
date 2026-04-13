import { Button } from "@/components/ui/button"
import { MessageSquare } from "lucide-react"

export function Hero() {
  return (
    <section className="relative min-h-[80vh] flex items-center justify-center px-6 py-24">
      {/* Subtle gradient overlay */}
      <div className="absolute inset-0 bg-[radial-gradient(ellipse_at_top,_var(--tw-gradient-stops))] from-primary/10 via-transparent to-transparent" />
      
      <div className="relative z-10 max-w-4xl mx-auto text-center">
        <div className="inline-flex items-center gap-2 px-4 py-2 rounded-full border border-border bg-secondary/50 mb-8">
          <MessageSquare className="w-4 h-4 text-primary" />
          <span className="text-sm text-muted-foreground">SMS Automation for Trades</span>
        </div>
        
        <h1 className="text-4xl md:text-6xl lg:text-7xl font-bold tracking-tight mb-6 text-balance">
          <span className="text-foreground">Stop losing jobs to</span>
          <br />
          <span className="text-primary">no-shows.</span>
        </h1>
        
        <p className="text-lg md:text-xl text-muted-foreground max-w-2xl mx-auto mb-10 text-pretty">
          Automated SMS confirmations, no-show alerts, and review requests built for HVAC, plumbing, and electrical businesses.
        </p>
        
        <div className="flex flex-col sm:flex-row gap-4 justify-center">
          <Button size="lg" className="text-base px-8 py-6 bg-primary hover:bg-primary/90 text-primary-foreground">
            Start Free Trial
          </Button>
          <Button size="lg" variant="outline" className="text-base px-8 py-6 border-border hover:bg-secondary">
            See How It Works
          </Button>
        </div>
        
        <p className="mt-6 text-sm text-muted-foreground">
          30-day free trial • No credit card required
        </p>
      </div>
    </section>
  )
}
