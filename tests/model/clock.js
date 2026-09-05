// A virtual clock with two independently advanceable axes.
//
// This is the instrument AC-041 requires. Schedule.js takes `now` (monotonic)
// and `nowWall` (wall) as separate arguments precisely so that a system suspend
// can be simulated — wall time jumps forward while monotonic time stands still
// — and a clock that advanced both together could not express that. A test
// using Date.now() for both would pass against a scheduler that confused the
// two axes, which is the bug the separation exists to prevent.
//
// Units are SECONDS on both axes, matching Schedule.js.

function createClock(options) {
  const opts = options || {}
  const state = {
    // Monotonic starts at a non-zero, non-round value so that a function which
    // accidentally treats "no deadline" as 0, or compares against a bare
    // interval, cannot pass by coincidence.
    mono: opts.mono === undefined ? 1000 : opts.mono,
    // Wall starts at a fixed instant — 2026-01-01T00:00:00Z — so that
    // Retry-After HTTP-date cases can be written as literal dates.
    wall: opts.wall === undefined ? 1767225600 : opts.wall
  }

  return {
    now: function () { return state.mono },
    nowWall: function () { return state.wall },

    // The ordinary case: time passes for everything.
    advance: function (seconds) {
      state.mono += seconds
      state.wall += seconds
      return this
    },

    // The suspend signature: the machine was asleep, so the wall clock moved
    // and the monotonic clock did not.
    suspend: function (seconds) {
      state.wall += seconds
      return this
    },

    // The opposite, and the reason `advance` is not the only mutator: a user
    // correcting a badly-set system clock, or NTP stepping it, moves wall time
    // without any monotonic time passing. Scheduling must not react.
    advanceMonotonic: function (seconds) {
      state.mono += seconds
      return this
    },

    // Both axes as the argument object every Schedule.js reducer takes, so
    // call sites read `clock.at()` rather than repeating two field names.
    at: function (extra) {
      const out = { now: state.mono, nowWall: state.wall }
      for (const key in extra) {
        if (Object.prototype.hasOwnProperty.call(extra, key)) out[key] = extra[key]
      }
      return out
    }
  }
}

module.exports = { createClock: createClock }
