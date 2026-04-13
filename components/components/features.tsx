import { CheckCircle2, Bell, Star } from "lucide-react"

const features = [
  {
    icon: CheckCircle2,
    title: "Automated Confirmations",
    description: "Customers automatically receive a text to confirm their appointment—no phone calls needed.",
  },
  {
    icon: Bell,
    title: "No-Show Alerts",
    description: "Get instantly notified if a customer doesn't confirm, so you can follow up or reschedule.",
  },
  {
    icon: Star,
    title: "Review Requests",
    description: "After the job is done, we send a friendly Google review request to help grow your reputation.",
  },
]

export function Features() {
  return (
    <section className="px-6 py-24 border-t border-border">
      <div className="max-w-6xl mx-auto">
        <div className="text-center mb-16">
          <h2 className="text-3xl md:text-4xl font-bold text-foreground mb-4">
            Everything you need to fill your schedule
          </h2>
          <p className="text-muted-foreground text-lg max-w-2xl mx-auto">
            Three simple automations that save you hours every week and keep your calendar full.
          </p>
        </div>
        
        <div className="grid md:grid-cols-3 gap-8">
          {features.map((feature) => (
            <div
              key={feature.title}
              className="p-8 rounded-xl border border-border bg-card hover:border-primary/50 transition-colors"
            >
              <div className="w-12 h-12 rounded-lg bg-primary/10 flex items-center justify-center mb-6">
                <feature.icon className="w-6 h-6 text-primary" />
              </div>
              <h3 className="text-xl font-semibold text-foreground mb-3">
                {feature.title}
              </h3>
              <p className="text-muted-foreground leading-relaxed">
                {feature.description}
              </p>
            </div>
          ))}
        </div>
      </div>
    </section>
  )
}
