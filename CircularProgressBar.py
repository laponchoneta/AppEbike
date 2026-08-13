from kivy.properties import ListProperty, NumericProperty, StringProperty
from kivy.uix.anchorlayout import AnchorLayout
from kivy.properties import Clock


class CircularProgressBar(AnchorLayout):
    bar_color = ListProperty([0,1,0])
    bar_bg_color = ListProperty([0.7,0.7,0.7])
    bar_width = NumericProperty(10)
    set_value = NumericProperty(5)
    text = StringProperty('0%')
    font_size = NumericProperty(40)
    tick = NumericProperty(1)
    start_value = NumericProperty(0)
    end_value = NumericProperty(360)
    counter = 0

    """def __init__(self, **kwargs):
        super(CircularProgressBar,self).__init__(**kwargs)
        Clock.schedule_once(self.animate, 0)

    def animate(self,*args):
        Clock.schedule_interval(self.percent_counter, 1)

    def percent_counter(self,*args):
        if self.counter < self.value:"""

