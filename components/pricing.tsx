import { Button } from "@/components/ui/button"
import { Check } from "lucide-react"

const included = [
  "Unlimited appointment confirmations",
  "No-show alerts via SMS",
  "Automated review requests",
  "Simple job management dashboard",
  "Email support",
]

export function Pricing() {
  return (
    <section className="px-6 py-24 border-t border-border">
      <div className="max-w-xl mx-auto">
        <div className="text-center mb-12">
          <h2 className="text-3xl md:text-4xl font-bold text-foreground mb-4">
            Simple, honest pricing
          </h2>
          <p className="text-muted-foreground text-lg">
            One plan. No hidden fees. Cancel anytime.
          </p>
        </div>
        
        <div className="p-8 md:p-10 rounded-2xl border border-primary/50 bg-card">
          <div className="text-center mb-8">
            <div className="flex items-baseline justify-center gap-2">
              <span className="text-5xl md:text-6xl font-bold text-foreground">$79</span>
              <span className="text-muted-foreground text-lg">/month</span>
            </div>
            <p className="text-primary mt-3 font-medium">30-day free trial included</p>
          </div>
          
          <ul className="space-y-4 mb-8">
            {included.map((item) => (
              <li key={item} className="flex items-center gap-3">
                <Check className="w-5 h-5 text-primary flex-shrink-0" />
                <span className="text-foreground">{item}</span>
              </li>
            ))}
          </ul>
          
          <Button className="w-full py-6 text-base bg-primary hover:bg-primary/90 text-primary-foreground">
            Start Your Free Trial
          </Button>
          
          <p className="text-center text-sm text-muted-foreground mt-4">
            No contracts. No credit card required to start.
          </p>
        </div>
      </div>
    </section>
  )
}
