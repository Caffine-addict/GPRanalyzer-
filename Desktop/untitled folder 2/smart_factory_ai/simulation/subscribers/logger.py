class LoggerSubscriber:

    def __call__(self, event):

        print(

            f"[{event.timestamp.strftime('%H:%M:%S')}] "

            f"{event.machine:<8}"

            f"{event.board_id:<15}"

            f"{event.status}"

        )