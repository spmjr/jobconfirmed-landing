const steps = [
  {
    number: "01",
    title: "Add a job",
    description: "Enter your customer's name, phone number, and appointment time in under 30 seconds.",
  },
  {
    number: "02",
    title: "We text your customer",
    description: "Your customer receives an automated confirmation text—they simply reply to confirm.",
  },
  {
    number: "03",
    title: "You get notified",
    description: "Get alerts for confirmations, no-responses, and review completions right to your phone.",
  },
]

export function HowItWorks() {
  return (
    <section className="px-6 py-24 border-t border-border">
      <div className="max-w-6xl mx-auto">
        <div className="text-center mb-16">
          <h2 className="text-3xl md:text-4xl font-bold text-foreground mb-4">
            How it works
          </h2>
          <p className="text-muted-foreground text-lg max-w-2xl mx-auto">
            Set up takes 5 minutes. Start reducing no-shows today.
          </p>
        </div>
        
        <div className="grid md:grid-cols-3 gap-8">
          {steps.map((step) => (
            <div key={step.number} className="relative">
              <div className="text-6xl font-bold text-primary mb-4">
                {step.number}
              </div>
              <h3 className="text-xl font-semibold text-foreground mb-3">
                {step.title}
              </h3>
              <p className="text-muted-foreground leading-relaxed">
                {step.description}
              </p>
            </div>
          ))}
        </div>
      </div>
    </section>
  )
}
