window.TRANSFER_DATA = {
  config: {
    windowName: 'Summer 2026',
    tagline: 'credibility, not clickbait',
    deadline: '2026-09-01T17:00:00Z',
    deadlineLabel: 'Deadline day: Sep 1, 6pm BST',
    updated: 'Jul 18, 2026',
    sourcesNote: 'Tier 1 sources weighted heaviest.',
    provenClubs: ['Ajax', 'Benfica', 'Sporting CP', 'Eintracht Frankfurt', 'Brighton'],
  },
  deals: [
    // Arsenal's marquee move
    { p:'Ellis Hartley', from:'Brighton', to:'Arsenal', fee:42, age:22, pos:'AM',
      status:'agreed', date:'Jul 16', tier:1, src:'Sky Sports', cred:74,
      note:"Arsenal moving early for a profile they've chased for two windows." },
    { p:'Rafael Moreno', from:'Benfica', to:'Newcastle', fee:55, age:26, pos:'ST',
      status:'medical', date:'Jul 17', tier:1, src:'The Athletic', cred:88,
      note:'Newcastle answering the striker question early.' },
    { p:'Tunde Okafor', from:'Ajax', to:'Everton', fee:18, age:24, pos:'CM',
      status:'talks', date:'Jul 10', tier:2, src:'Telegraph', cred:55,
      note:"Everton's only realistic midfield upgrade at this price." },
  ],
  clubs: {
    Arsenal: { needs:'Left centre back', ctx:'Front-loading the window.' },
    Newcastle: { needs:'Right wing', ctx:'One marquee striker, then done.' },
    Everton: { needs:'Central midfield', ctx:'Budget built around stadium debt.' },
    Brighton: { needs:'Nothing', ctx:'Selling club, as always.' },
    Benfica: { needs:'Nothing', ctx:'Selling club.' },
    Ajax: { needs:'Nothing', ctx:'Selling club.' },
  },
};
