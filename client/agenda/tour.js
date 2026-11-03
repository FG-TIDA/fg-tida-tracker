// The inherited agenda guide needs no third-party object-merging dependency.
// FG-TIDA uses its own agenda view; retain a native guide for the legacy bundle.
class Tour {
  steps = []
  position = -1
  dialog = null

  addSteps (steps) { this.steps = steps }
  cancel = () => {
    this.dialog?.close()
    this.dialog?.remove()
    this.dialog = null
  }
  start = () => { this.position = -1; this.next() }
  next = () => {
    this.cancel()
    let step
    do { step = this.steps[++this.position] } while (step?.showOn && !step.showOn())
    if (!step) return
    const dialog = document.createElement('dialog')
    dialog.className = 'rounded border p-4'
    dialog.setAttribute('aria-labelledby', 'agenda-guide-heading')
    const heading = document.createElement('h2')
    heading.id = 'agenda-guide-heading'
    heading.textContent = step.title
    const description = document.createElement('p')
    description.textContent = new DOMParser().parseFromString(step.text, 'text/html').body.textContent
    const buttons = document.createElement('div')
    buttons.className = 'd-flex gap-2 justify-content-end'
    const exit = document.createElement('button')
    exit.className = 'btn btn-secondary'
    exit.textContent = 'Close'
    exit.addEventListener('click', this.cancel)
    const next = document.createElement('button')
    next.className = 'btn btn-primary'
    next.textContent = this.position === this.steps.length - 1 ? 'Finish' : 'Next'
    next.addEventListener('click', this.next)
    buttons.append(exit, next)
    dialog.append(heading, description, buttons)
    document.body.append(dialog)
    this.dialog = dialog
    dialog.addEventListener('cancel', this.cancel)
    dialog.showModal()
  }
}

export function initTour ({ mobileMode, pickerMode }) {
  const tour = new Tour({
    useModalOverlay: true,
    defaultStepOptions: {
      classes: 'shepherd-theme-custom',
      scrollTo: false,
      modalOverlayOpeningPadding: 8,
      modalOverlayOpeningRadius: 4,
      popperOptions: {
        modifiers: [
          {
            name: 'offset', 
            options: {
              offset: [0,20]
            }
          }
        ]
      }
    }
  })
  const defaultButtons = [
    {
      text: 'Exit',
      action: tour.cancel,
      secondary: true
    },
    {
      text: 'Next',
      action: tour.next
    }
  ]

  // STEPS

  tour.addSteps([
    {
      title: 'Filter Areas + Groups',
      text: 'You can filter the list of sessions by areas or working groups you\'re interested in. The filters you select here also apply to the <strong>Calendar View</strong> and persist even if you come back to this page later.',
      attachTo: {
        element: mobileMode ? '.agenda-mobile-bar > button:first-child' : '#agenda-quickaccess-filterbyareagroups-btn',
        on: mobileMode ? 'top' : 'left'
      },
      buttons: defaultButtons
    },
    {
      title: 'Pick Sessions',
      text: 'Alternatively select <strong>individual sessions</strong> from the list to build your own schedule.',
      attachTo: {
        element: pickerMode ? '.agenda-quickaccess-btnrow' : '#agenda-quickaccess-picksessions-btn',
        on: 'left'
      },
      buttons: defaultButtons,
      showOn: () => !mobileMode
    },
    {
      title: 'Calendar View',
      text: 'View the current list of sessions in a <strong>calendar view</strong>, by week or by day. The filters you selected above also apply in this view.',
      attachTo: {
        element: mobileMode ? '.agenda-mobile-bar > button:nth-child(2)' : '#agenda-quickaccess-calview-btn',
        on: mobileMode ? 'top' : 'left'
      },
      buttons: defaultButtons
    },
    {
      title: 'Add to your calendar',
      text: 'Add the current list of sessions to your personal calendar application, in either <strong>webcal</strong> or <strong>ics</strong> format.',
      attachTo: {
        element: mobileMode ? '.agenda-mobile-bar > button:nth-child(3)' : '#agenda-quickaccess-addtocal-btn',
        on: mobileMode ? 'top' : 'left'
      },
      buttons: defaultButtons
    },
    {
      title: 'Search Events',
      text: 'Filter the list of sessions by searching for <strong>specific keywords</strong> in the title, location, acronym, notes or group name. Click the button again to close the search and discard its filtering.',
      attachTo: {
        element: '.agenda-table-search',
        on: 'top'
      },
      buttons: defaultButtons
    },
    {
      title: 'Assign Colors to Events',
      text: 'Assign colors to individual events to keep track of those you find interesting, wish to attend or define your own colors / descriptions from the <strong>Settings</strong> panel.',
      attachTo: {
        element: '.agenda-table-colorpicker',
        on: 'top'
      },
      buttons: defaultButtons
    },
    {
      title: 'Sessions',
      text: 'View the session materials by either clicking on its title or using the <strong>Show meeting materials</strong> button on the right. You can locate the room holding this event on the floor plan by clicking on the location name.',
      attachTo: {
        element: () => document.querySelector('.agenda-table-display-event'),
        on: 'top'
      },
      buttons: [
        {
          text: 'Finish',
          action: tour.next
        }
      ],
      modalOverlayOpeningPadding: 0,
      modalOverlayOpeningRadius: 2
    }
  ])

  return tour
}
